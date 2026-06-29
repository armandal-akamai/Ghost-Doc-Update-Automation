"""
Log Format Agent Orchestrator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This module defines and compiles a stateful LangGraph workflow designed to safely 
automate updates to Akamai LogFormat XML configurations. It parses raw text instructions 
(e.g., Jira ticket descriptions), extracts structured modifications using an LLM, 
compiles them into a YAML configuration, and applies them to a master XML file.

Workflow Architecture:
    1. Extract (``unified_extractor_node``): Parses human input via gpt-4o-mini to 
       generate a strictly validated Pydantic model (``CompilerPayload``).
    2. Route (``route_after_extraction``): Evaluates the extracted data for missing 
       mandatory fields.
    3. Ask Human (``ask_human_node``): An interrupt node triggered if required 
       attributes are missing from the user's initial prompt.
    4. Compile (``yaml_compiler_node``): Transforms the structured Pydantic payload 
       into a clean YAML string.
    5. Review (``review_yaml_node``): An interrupt node that pauses execution, allowing 
       a human to review and manually edit the staged YAML.
    6. Deploy (``deployment_node``): Writes the approved YAML to an isolated sandbox 
       and triggers the underlying ``deploy_to_master`` XML parsing engine.

State Management & Concurrency:
    The graph utilizes a ``MemorySaver`` checkpointer to pause at human-in-the-loop 
    checkpoints. To support concurrent web executions, it relies on dynamically 
    generated sandboxes (``create_sandbox_config``) to isolate files per thread.
"""

from typing import TypedDict, List, Optional
from filelock import FileLock
from P4 import P4, P4Exception
import yaml
from langgraph.graph import StateGraph, START, END
from langchain_core.prompts import ChatPromptTemplate
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig
import os
import dotenv
from LogFormat.graph import CompilerPayload
from LogFormat.deploy import deploy_to_master
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
    
    This node takes the user's raw prompt, wraps it in the system instructions, 
    and invokes a structured LLM call. It also performs a preliminary 
    check for missing metadata (like author, version, or target IDs).

    :param state: The current state of the graph.
    :return: A dictionary containing the parsed Pydantic payload and a list of any missing attributes.
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
    
    Strips out any empty or null values from the structured payload and formats 
    it into a clean YAML representation, ready for human review.

    :param state: The current state of the graph.
    :return: A dictionary mapping the compiled YAML string to the state.
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
    
    This function ensures concurrent agent runs do not overwrite each other's XML 
    files. It creates an isolated workspace directory inside ``agent_workspaces/`` 
    and seeds it with a fresh copy of the master XML and schema map.

    :param ticket_id: The unique identifier (e.g., Jira ticket number) used to name the sandbox.
    :return: A LangGraph RunnableConfig dictionary containing strict, isolated file paths.
    """
    # Define the isolated folder for this specific ticket
    base_workspace = os.path.join(ROOT_DIR, "agent_workspaces")
    ticket_workspace = os.path.join(base_workspace, ticket_id)
    
    # Create the folder if it doesn't exist
    os.makedirs(ticket_workspace, exist_ok=True)

    # Return the strict, dynamic paths for LangGraph
    return {
        "configurable": {
            "thread_id": f"{ticket_id}",
            "yaml_file": os.path.join(ticket_workspace, "approved_input.yaml"),
            "schema_file": os.path.join(BASE_DIR, "schema_map.json"),
        }
    }

def deployment_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Writes the approved YAML to disk and triggers the deployment script.
    
    This node executes the final side-effects of the graph. It enforces a fail-fast 
    rule to ensure all required sandbox paths are present in the configuration. Once 
    verified, it writes the in-memory YAML to disk and hands execution off to the 
    underlying ``deploy_to_master`` script.

    :param state: The current state of the graph.
    :param config: The graph configuration containing isolated sandbox paths.
    :return: A dictionary containing the final deployment status string.
    :raises ValueError: If the required isolated file paths are missing from the config.
    """
    # 1. Fetch paths from config
    paths = config.get("configurable", {})
    yaml_file = paths.get("yaml_file")
    schema_file = paths.get("schema_file")
    master_file = os.path.join(BASE_DIR, "log-format.xml") 
    ticket_id = paths.get("thread_id", "Unknown_Ticket")
    
    # 2. FAIL-FAST RULE: If any path is missing, instantly kill the process to prevent collisions
    if not all([yaml_file, schema_file, master_file]):
        raise ValueError(
            "CRITICAL ERROR: Missing explicit sandbox file paths in configuration. "
            "To prevent multi-user collisions, the web application must provide unique, "
            "isolated paths for yaml_file, schema_file, master_file."
        )
    
    # 3. Ensure the sandboxed directory actually exists before trying to write to it
    workspace_dir = os.path.dirname(yaml_file)
    if workspace_dir and not os.path.exists(workspace_dir):
        os.makedirs(workspace_dir, exist_ok=True)

    # 4. Write the human-reviewed YAML string to disk so deploy.py can read it
    with open(yaml_file, "w", encoding="utf-8") as f:
        f.write(state["yaml_string"])
        
    print(f"\n[SYSTEM] Kicking off Perforce deployment")
    target_xml_path = os.path.join(BASE_DIR, "log-format.xml")
    lock_path = os.path.join(BASE_DIR, "perforce_deploy.lock")

    lock = FileLock(lock_path, timeout=60)

    try:
        # 4. THE QUEUE: Process waits here until the lock is available
        with lock:
            print(f"[SYSTEM] Lock acquired. Connecting to Perforce...")
            p4 = P4()
            p4.port = os.getenv("P4PORT")
            p4.user = os.getenv("P4USER")
            p4.client = os.getenv("P4CLIENT")
            p4.exception_level = 1
            
            p4.connect()
            
            try:
                # Step A: Get the absolute latest file from the server
                p4.run_sync("-f", target_xml_path)
                
                # Step B: Lock it for editing in our workspace
                p4.run_edit(target_xml_path)
                
                # Step C: Execute surgical injection directly on the master file
                deploy_to_master(
                    yaml_file=yaml_file,
                    schema_file=schema_file,
                    master_file=target_xml_path,
                    output_file=target_xml_path
                )
                
                # Step D: Auto-Submit the changes
                commit_msg = f"Automated log-format.xml update for {ticket_id}"
                p4.run_submit("-d", commit_msg, target_xml_path)
                
                return {"deployment_status": f"SUCCESS: Perforce update deployed for {ticket_id}."}

            except P4Exception as p4e:
                # FAIL-SAFE: If Perforce throws an error, revert the file
                print(f"[ERROR] Perforce transaction failed: {p4e}. Reverting...")
                p4.run_revert(target_xml_path)
                raise ValueError(f"Perforce Error: {p4e}")
                
            except Exception as e:
                # FAIL-SAFE: If deploy_to_master crashes, revert the file
                print(f"[ERROR] Deployment script failed: {e}. Reverting...")
                p4.run_revert(target_xml_path)
                raise ValueError(f"Deployment Engine Error: {e}")
                
            finally:
                # Always close the connection to prevent memory/socket leaks
                p4.disconnect()

    except TimeoutError:
        raise ValueError("Deployment Timeout: The queue is too busy. Please try again in a few minutes.")
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
