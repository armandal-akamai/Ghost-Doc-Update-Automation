from typing import TypedDict, List, Optional, Literal
import yaml
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langchain_core.prompts import ChatPromptTemplate
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig
import os
import dotenv
from graph import CompilerPayload
from deploy import deploy_to_master
import tempfile
import subprocess
import sys
from xmlGenerator import UniversalXMLGenerationEngine

dotenv.load_dotenv()

# GRAPH STATE MEMORY

class AgentState(TypedDict):
    user_input: str                           # Raw prompt from the engineer
    parsed_payload: Optional[CompilerPayload] # The structured Pydantic object from the LLM
    missing_attributes: List[str]             # Checklist of fields the user forgot
    yaml_string: Optional[str]                # Final compiled YAML (will be None for now)
    validation_error: Optional[str]           # Execution errors (will be None for now)


# GRAPH NODES

def unified_extractor_node(state: AgentState) -> dict:
    """
    Cognitive node: Extracts clean schema models from raw text inputs.
    """
    raw_prompt = state["user_input"]
    system_instruction = (
        "You are an expert compiler extraction assistant for an Akamai logging platform.\n"
        "Your task is to analyze the user's request and map it strictly into the schema.\n\n"
        "CRITICAL ROUTING RULES:\n"
        "1. If the user asks to add/append a changelog, set action_type to 'append_changelog', "
        "extract the author, date, and version, and ONLY fill the changelog_payload. Leave logline_payload null.\n"
        "2. If the user asks to modify a log field, set action_type to 'update_logline', "
        "EXTRACT THE TARGET LOG LINE ID (e.g., 'r', 'f'), and ONLY fill the logline_payload. "
        "CRITICAL: You MUST place any extracted 'standalone_fields' or 'log_field_groups' INSIDE the 'log_fields' wrapper object. "
        "Leave changelog_payload null.\n"
        "3. NESTED LAZINESS: Do not hallucinate deep nested data. If a sub_field, bitmask, or enum is not "
        "explicitly mentioned by the user, leave it null. Do not generate empty nested objects.\n"
        "4. NEVER hallucinate IDs. If a user mentions 'r77', the target log line ID is 'r'."
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_instruction),
        ("human", "{input}")
    ])
    
    llm = init_chat_model(
        "openai/gpt-oss-120b:free",
        model_provider="openai",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.0
    )
    structured_llm = llm.with_structured_output(CompilerPayload)
    
    extractor_chain = prompt | structured_llm
    extracted_data: CompilerPayload = extractor_chain.invoke({"input": raw_prompt})
    
    # Quick check for missing metadata validation
    missing = []
    if extracted_data.action_type == "append_changelog":
        payload = extracted_data.changelog_payload
        if not payload or not payload.author or not payload.ghost_version:
            missing.append("changelog details (author/version/date)")
    elif extracted_data.action_type == "update_logline":
        sorted_data = extracted_data.logline_payload
        if not sorted_data or not sorted_data.id:
            missing.append("log line target ID token (e.g., 'r', 'f')")

    return {
        "parsed_payload": extracted_data,
        "missing_attributes": missing
    }

def yaml_compiler_node(state: AgentState) -> dict:
    """
    Deterministic node: Converts the validated Pydantic model into a YAML string.
    """
    # model_dump(exclude_none=True) strips out all the nulls
    payload_dict = state["parsed_payload"].model_dump(exclude_none=True, by_alias=True)
    
    # Dump to a clean YAML string
    yaml_str = yaml.dump(payload_dict, default_flow_style=False, sort_keys=False)
    
    return {"yaml_string": yaml_str}

def ask_human_node(state: AgentState) -> dict:
    """
    Placeholder node: In a real app, this pauses the graph to ask the user 
    for the missing fields identified in state['missing_attributes'].
    """
    print(f"\n[SYSTEM PAUSE] Missing required data: {state['missing_attributes']}")
    print("Routing to human for clarification...\n")
    return {} # No changes to state right now

def route_after_extraction(state: AgentState) -> str:
    """
    Conditional edge logic: Checks for missing attributes.
    """
    if len(state["missing_attributes"]) > 0:
        return "ask_human"
    
    return "yaml_compiler"

def review_yaml_node(state: AgentState) -> dict:
    """
    Signpost Node: Acts as a pause checkpoint so the human 
    can review and modify the generated YAML string.
    """
    return {}

def deployment_node(state: AgentState, config: RunnableConfig) -> dict:
    """Writes the approved YAML to disk and triggers the deployment script."""
    
    # 1. Fetch paths from config
    paths = config.get("configurable", {})
    yaml_file = paths.get("yaml_file", "approved_input.yaml")
    schema_file = paths.get("schema_file", "schema_map.json")
    master_file = paths.get("master_file", "log-format.xml")
    output_file = paths.get("output_file", "log-format-updated.xml")
    
    # 2. Write the human-reviewed YAML string to disk so deploy.py can read it
    with open(yaml_file, "w", encoding="utf-8") as f:
        f.write(state["yaml_string"])
        
    print(f"\n[SYSTEM] Kicking off deploy_to_master pipeline...")
    
    # 3. Hand off entirely to your existing deploy script!
    deploy_to_master(
        yaml_file=yaml_file,
        schema_file=schema_file,
        master_file=master_file,
        output_file=output_file
    )
    
    return {"deployment_status": "SUCCESS: Master deployment script completed."}
# GRAPH ASSEMBLY & COMPILATION

memory = MemorySaver()
workflow = StateGraph(AgentState)

# 1. Add all nodes to the registry
workflow.add_node("unified_extractor", unified_extractor_node)
workflow.add_node("yaml_compiler", yaml_compiler_node)
workflow.add_node("ask_human", ask_human_node)
workflow.add_node("deployment", deployment_node)

# 2. Define the starting edge
workflow.add_edge(START, "unified_extractor")

# 3. Add the Conditional Edge (The Router)
workflow.add_conditional_edges(
    "unified_extractor",      # The node we are coming from
    route_after_extraction,   # The routing function to evaluate
    {
        # Map the function's string output to the actual node names
        "yaml_compiler": "yaml_compiler",
        "ask_human": "ask_human"
    }
)

# 3. Define the endpoints
workflow.add_edge("ask_human", "unified_extractor")

# 1. Register the new review node
workflow.add_node("review_yaml", review_yaml_node)

# 2. Update the edges: Compiler flows into Review, Review flows into Downstream Execution
workflow.add_edge("yaml_compiler", "review_yaml")
workflow.add_edge("review_yaml", "deployment")
workflow.add_edge("deployment", END)

# 3. CRITICAL: Add "review_yaml" to the interrupt list!
app = workflow.compile(
    checkpointer=memory, 
    interrupt_before=["ask_human", "review_yaml"] #Both brakes active

)

# =====================================================================
# PHASE 5: INTERACTIVE EXECUTION PASS
# =====================================================================
if __name__ == "__main__":
    print("STARTING AGENT WITH HUMAN-IN-THE-LOOP GATING")
# The raw user request that successfully targets standalone_fields without hallucinating a name
    user_input = """
    Update the 'r' log line. Inside field '70', under the 'named-sub-fields' container, generate a brand new 'named-field' modeled exactly after the 'wco' layout rules with the following configurations:

    1. TOP-LEVEL ATTRIBUTES:
       - Set the 'id' attribute to 'txa'
       - Set the 'since' attribute to '22.5.0'
       - Set the 'reporting-level' attribute to 'billing'
       - Set the top-level 'doc' string to 'Tracks detailed multi-tiered transaction status anomalies.'

    2. SECOND-LEVEL NESTED STRUCTURE (sub-fields):
       - Add a nested 'sub-fields' block inside this named-field.
       - Set the internal 'splitter' tag 'pattern' attribute to a pipe character '|'.

    3. POSITIONALS (sub-field entries):
       - Create 'sub-field' entry id '1' with 'doc' set to 'Attempted action indicators'. Inside it, add a 'sub-chars' list containing:
         * sub-char id '0' with doc 'none'
         * sub-char id '1' with doc 'database-write'
         * sub-char id '2' with doc 'token-validation'
       - Create 'sub-field' entry id '2' with 'doc' set to 'Mitigation strategy status'. Inside it, add a 'sub-chars' list containing:
         * sub-char id '?' with doc 'unknown status'
         * sub-char id 's' with doc 'success'
         * sub-char id 'f' with doc 'failed fallback'

    Ensure absolutely no structural layers, id tags, or documentation fields are skipped, omitted, or summarized.
    """
    initial_input = {
        "user_input": (user_input)
    }

# The configuration dictionary required by LangGraph's checkpointer to track the session state
    config = {
        "configurable": {
            "thread_id": "production_patch_session_001",
            "yaml_file": "approved_input.yaml",      # The temporary file the node will write the approved YAML to
            "schema_file": "schema_map.json",        # Your JSON schema blueprint
            "master_file": "log-format.xml",         # The target XML file you want to inject into
            "output_file": "log-format-updated.xml"  # The final saved output file
        }
    }

    # 1. Kick off the initial stream pass
    for event in app.stream(initial_input, config, stream_mode="updates"):
        for node_name, state_update in event.items():
            print(f" NODE FINISHED: {node_name}")

    # 2. Process active State Interrupts
    state = app.get_state(config)
    while state.next:
        next_node = state.next[0]

        # --- INTERRUPT A: MISSING REQUIREMENT PATH ---
        if next_node == "ask_human":
            missing = state.values.get("missing_attributes", ["unknown items"])
            user_correction = input(f"\n[HUMAN REQUIRED] Missing {missing}. Type it here: ")
            current_input = state.values["user_input"]
            updated_input = current_input + f"\n\nUser clarification: {user_correction}"
            
            app.update_state(config, {"user_input": updated_input}, as_node="ask_human")
            
            # Resume graph execution
            for event in app.stream(None, config, stream_mode="updates"):
                for node_name, _ in event.items():
                    print(f" NODE FINISHED: {node_name}")
            
            state = app.get_state(config)

        # --- INTERRUPT B: YAML INSPECTION & REWRITE PATH ---
        elif next_node == "review_yaml":
            current_yaml = state.values.get("yaml_string", "")
            
            print("CODE REVIEW: STAGED PRODUCTION YAML PAYLOAD")
            print(current_yaml.strip())
            print("====================================================")
            
            choice = input("\nAction? [Press ENTER to Approve & Deploy] or [Type 'e' to Edit]: ").strip().lower()
            if choice == 'e':
                # 2. Write the current YAML out to a temporary file
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.yaml', encoding='utf-8') as temp_file:
                    temp_path = temp_file.name
                    temp_file.write(current_yaml)
                
                print(f"\n[SYSTEM] Creating temporary file at: {temp_path}")
                
                # 3. Automatically pop open the file using the host OS default text editor
                try:
                    if sys.platform == 'darwin':       # macOS 
                        subprocess.run(["open", "-t", temp_path])
                    elif os.name == 'nt':              # Windows fallback
                        os.startfile(temp_path)
                    else:                              # Linux fallback
                        subprocess.run(["xdg-open", temp_path])
                except Exception as e:
                    print( f"⚠️ Could not launch editor automatically: {e}")
                    print(f"Please open and edit this file path manually: {temp_path}")

                # 4. Hold execution until the user saves edits in their editor and hits ENTER here
                input("\n📝 File opened! Modify the schema, SAVE your changes, then press ENTER here to commit...")
                
                # 5. Read the updated contents back into memory
                with open(temp_path, 'r', encoding='utf-8') as temp_file:
                    manually_corrected_yaml = temp_file.read()
                    
                # 6. Clean up and delete the temporary file from disk
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                
                # 7. Inject the updated edits seamlessly straight back into the LangGraph state
                app.update_state(config, {"yaml_string": manually_corrected_yaml}, as_node="review_yaml")
                print("\n [SYSTEM] Local adjustments saved successfully.")

            print("\n Releasing safety brake. Proceeding to downstream deployment execution...")

# Resume graph execution (flows directly into downstream_execution node)
            for event in app.stream(None, config, stream_mode="updates"):
                for node_name, _ in event.items():
                    print(f" NODE FINISHED: {node_name}")

            state = app.get_state(config)

