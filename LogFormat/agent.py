from typing import TypedDict, List, Optional
import yaml
from langgraph.graph import StateGraph, START, END
from langchain_core.prompts import ChatPromptTemplate
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig
import os
import dotenv
from graph import CompilerPayload
from deploy import deploy_to_master
import shutil

dotenv.load_dotenv()
os.environ["LANGGRAPH_ALLOWED_MSGPACK_MODULES"] = "graph"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
# TODO -> Move Memory Saver to a database.
# TODO -> Add an MCP server for fetching the details of Jira Ticket.
# GRAPH STATE MEMORY

class AgentState(TypedDict):
    user_input: str                           # Raw prompt from the engineer
    parsed_payload: Optional[CompilerPayload] # The structured Pydantic object from the LLM
    missing_attributes: List[str]             # Checklist of fields the user forgot
    yaml_string: Optional[str]                # Final compiled YAML (will be None for now)
    validation_error: Optional[str]           # Execution errors (will be None for now)
    deployment_status: Optional[str]          # The deployment status


# GRAPH NODES

def unified_extractor_node(state: AgentState) -> dict:
    """
    Cognitive node: Extracts clean schema models from raw text inputs.
    """
    raw_prompt = state["user_input"]
    with open(os.path.join(BASE_DIR, "systemPrompt.txt"), "r") as f:
     system_instruction = f.read()

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_instruction),
        ("human", "{input}")
    ])
    
    llm = init_chat_model(
    "gpt-4o-mini",                     # The model you want to target on GitHub
    model_provider="openai",            # Still uses the openai underlying class
    base_url="https://models.github.ai/inference",  # Point to GitHub's inference endpoint
    api_key=os.getenv("GITHUB_TOKEN"),  # Make sure GITHUB_TOKEN is defined in your .env file
    temperature=0.0
    )
    structured_llm = llm.with_structured_output(CompilerPayload)
    
    extractor_chain = prompt | structured_llm
    print("\n" + "="*50)
    print("WHAT THE LLM IS RECEIVING:")
    print("="*50)
    
    # Format the prompt exactly how LangChain will send it
    formatted_messages = prompt.format_messages(input=raw_prompt)
    for msg in formatted_messages:
        print(f"[{msg.type.upper()} ROLE]:")
        print(f"{msg.content}\n")
        print("-" * 50)
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

def create_sandbox_config(ticket_id: str) -> dict:
    """
    Generates a unique sandboxed configuration dictionary for a specific Jira ticket.
    Also handles seeding the sandbox with the master schema/xml files.
    """
    # Define the isolated folder for this specific ticket
    base_workspace = os.path.join(ROOT_DIR, "agent_workspaces")
    ticket_workspace = os.path.join(base_workspace, ticket_id)
    
    # Create the folder if it doesn't exist
    os.makedirs(ticket_workspace, exist_ok=True)
    
    shutil.copy(os.path.join(BASE_DIR, "log-format.xml"), os.path.join(ticket_workspace, "log-format.xml"))
    shutil.copy(os.path.join(BASE_DIR, "schema_map.json"), os.path.join(ticket_workspace, "schema_map.json"))

    # Return the strict, dynamic paths for LangGraph
    return {
        "configurable": {
            "thread_id": f"jira_session_{ticket_id}",
            "yaml_file": os.path.join(ticket_workspace, "approved_input.yaml"),
            "schema_file": os.path.join(ticket_workspace, "schema_map.json"),
            "master_file": os.path.join(ticket_workspace, "log-format.xml"),
            "output_file": os.path.join(ticket_workspace, "log-format-updated.xml")
        }
    }

def deployment_node(state: AgentState, config: RunnableConfig) -> dict:
    """Writes the approved YAML to disk and triggers the deployment script."""
    
    # 1. Fetch paths from config
    paths = config.get("configurable", {})
    yaml_file = paths.get("yaml_file")
    schema_file = paths.get("schema_file")
    master_file = paths.get("master_file")
    output_file = paths.get("output_file")
    
    # 2. FAIL-FAST RULE: If any path is missing, instantly kill the process to prevent collisions
    if not all([yaml_file, schema_file, master_file, output_file]):
        raise ValueError(
            "CRITICAL ERROR: Missing explicit sandbox file paths in configuration. "
            "To prevent multi-user collisions, the web application must provide unique, "
            "isolated paths for yaml_file, schema_file, master_file, and output_file."
        )
    
    # 3. Ensure the sandboxed directory actually exists before trying to write to it
    workspace_dir = os.path.dirname(yaml_file)
    if workspace_dir and not os.path.exists(workspace_dir):
        os.makedirs(workspace_dir, exist_ok=True)

    # 4. Write the human-reviewed YAML string to disk so deploy.py can read it
    with open(yaml_file, "w", encoding="utf-8") as f:
        f.write(state["yaml_string"])
        
    print(f"\n[SYSTEM] Kicking off deploy_to_master pipeline...")
    
    # 5. Hand off entirely to your existing deploy script!
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
workflow.add_node("review_yaml", review_yaml_node)

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

# 2. Update the edges: Compiler flows into Review, Review flows into Downstream Execution
workflow.add_edge("yaml_compiler", "review_yaml")
workflow.add_edge("review_yaml", "deployment")
workflow.add_edge("deployment", END)

# 3. CRITICAL: Add "review_yaml" to the interrupt list!
app = workflow.compile(
    checkpointer=memory, 
    interrupt_before=["ask_human", "review_yaml"] #Both brakes active

)
