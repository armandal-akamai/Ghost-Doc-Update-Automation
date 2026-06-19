import os
import subprocess
from typing import TypedDict, Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape
from langchain_core.runnables import RunnableConfig

# LangChain & LangGraph imports
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

# Import the Pydantic schema we built earlier
from pyd import MDRQueryTable 
import dotenv
#TODO-> last_updated, etc. -> Read the doc above the ghost_h3_server.xml file.
# 1. Define the Graph State

BASE_DIR = os.path.dirname(os.path.abspath(__file__)) 
ROOT_DIR = os.path.dirname(BASE_DIR)
dotenv.load_dotenv()
os.environ["LANGGRAPH_ALLOWED_MSGPACK_MODULES"] = "graph"

class GraphState(TypedDict):
    """Tracks the current state of data moving through our automation graph."""
    jira_ticket_text: str
    table_data: Optional[MDRQueryTable]
    xml_string: Optional[str]
    xml_filename: Optional[str]
    error: Optional[str]

# 2. Initialize your OpenRouter LLM with Structured Output
base_llm = init_chat_model(
    "gpt-4o-mini",                     # The model you want to target on GitHub
    model_provider="openai",            # Still uses the openai underlying class
    base_url="https://models.github.ai/inference",  # Point to GitHub's inference endpoint
    api_key=os.getenv("GITHUB_TOKEN"),  # Make sure GITHUB_TOKEN is defined in your .env file
    temperature=0.0
    )
structured_llm = base_llm.with_structured_output(MDRQueryTable)

# 3. Define the Graph Nodes (The Steps)
def extract_jira_node(state: GraphState) -> dict:
    """Node 1: Sends the raw text to the LLM and extracts structured data."""
    print("[Node: Extract] AI Agent is processing Jira ticket text via OpenRouter...")
    system_prompt = (
        "You are an expert Akamai system documentation agent tasked with extracting "
        "database table schema definitions from developer Jira tickets into a JSON schema.\n\n"
        
        "STRICT EXTRACTION RULES:\n"
        "1. XHTML WRAPPING: You MUST wrap the 'table_desc' and EVERY column 'description' "
        "in explicit, paired html paragraph tags. (e.g., '<p>Your text here</p>'). Never leave it as raw text.\n"
        "2. PROVIDERS: Look closely for mentioned networks or platforms like ESSL, FreeFlow, or NetStorage. "
        "Do NOT put the publisher or team name into the providers list.\n"
        "3. QUERY RESULTS: For the 'query_result' field, generate a clean, mock text-based output "
        "table printout showing sample rows. CRITICAL: Do NOT wrap the 'query_to_send' or 'query_result' "
        "in <pre> tags or any other HTML tags. Output plain text only.\n"
        "4. TYPES: If column data types are ambiguous or missing, default to 'string'."
    )
    try:
        # Invoke the structured LLM using standard LangChain formatting
        extracted_payload = structured_llm.invoke([
            ("system", system_prompt),
            ("user", f"Please process this closed Jira ticket text:\n\n{state['jira_ticket_text']}")
        ])
        
        # Update the graph state with the Pydantic object
        return {"table_data": extracted_payload, "error": None}
    
    except Exception as e:
        print(f"Extraction error: {e}")
        return {"error": str(e)}

def render_xml_node(state: GraphState) -> dict:
    """Node 2: Takes the Pydantic data and renders the XML string in memory."""
    if state.get("error"):
        return state

    table_data = state["table_data"]
    print(f"[Node: Render] Rendering XML in memory for review...")
    
    # autoescape handles the XML escaping safely and automatically
    env = Environment(
        loader=FileSystemLoader(BASE_DIR), 
        trim_blocks=True, 
        lstrip_blocks=True,
        autoescape=select_autoescape(['xml']) 
    )
    template = env.get_template('table_template.xml')
    final_xml = template.render(table=table_data)
    
    return {"xml_string": final_xml}

def save_xml_node(state: GraphState, config: RunnableConfig) -> dict:
    """Node 3: Takes the human-approved XML string and writes it to a sandboxed folder."""
    if state.get("error"):
        return state

    table_data = state["table_data"]
    approved_xml = state["xml_string"]
    
    # 1. Fetch the thread_id from the config to use as our sandbox folder name
    thread_id = config.get("configurable", {}).get("thread_id", "default_user")
    
    # 2. Create the isolated workspace path (e.g., workspaces/MDR-123/)
    workspace_dir = os.path.join(ROOT_DIR, "agent_workspaces", thread_id)
    os.makedirs(workspace_dir, exist_ok=True)
    
    # 3. Save the file inside the sandbox
    filename = os.path.join(workspace_dir, f"{table_data.name}.xml")
    
    print(f"[Node: Save] Human approved! Writing to disk at {filename}...")
    with open(filename, "w") as f:
        f.write(approved_xml)
        
    return {"xml_filename": filename}

def validate_xml_node(state: GraphState) -> dict:
    """Node 2.5: Validates the in-memory XML string using xmllint."""
    if state.get("error"):
        return state
        
    xml_string = state["xml_string"]
    print("[Node: Validate] Running xmllint validation...")
    
    try:
        # Run xmllint via subprocess. 
        # The "-" tells xmllint to read from standard input instead of a file.
        result = subprocess.run(
            ["xmllint", "--noout", "-"], 
            input=xml_string, 
            capture_output=True, 
            text=True
        )
        
        # returncode 0 means xmllint found zero errors
        if result.returncode == 0:
            print("XML Validation Passed!")
            return {"error": None}
        else:
            # If it fails, we capture the stderr output so you know exactly why
            error_msg = f"xmllint validation failed:\n{result.stderr}"
            print(f"{error_msg}")
            return {"error": error_msg}
            
    except FileNotFoundError:
        # A safety net just in case you run this on a machine without xmllint installed
        error_msg = "xmllint command not found on this system. Please install it."
        print(f"{error_msg}")
        return {"error": error_msg}

# 4. Build and Compile the Graph Workflow
workflow = StateGraph(GraphState)

# Add our processing blocks
workflow.add_node("extract_jira", extract_jira_node)
workflow.add_node("render_xml", render_xml_node)
workflow.add_node("save_xml", save_xml_node)
workflow.add_node("validate_xml", validate_xml_node)

# Set up the linear execution route
workflow.add_edge(START, "extract_jira")
workflow.add_edge("extract_jira", "render_xml")
workflow.add_edge("render_xml", "validate_xml")
workflow.add_edge("validate_xml", "save_xml")
workflow.add_edge("save_xml", END)

# Compile into an executable application
memory = MemorySaver()
app = workflow.compile(checkpointer=memory, interrupt_before=["validate_xml"])

# 5. Execute the graph
if __name__ == "__main__":
    # Sample developer note simulating closed ticket contents
    ticket_payload = """
    Please generate a new MDR Query Table with the following details:

    Name: cache_efficiency

    Publisher: Core Platform Engineering

    Owner: Jane Doe

    Table Description: Tracks cache hit rates and bypass reasons across all edge groups.

    Notes: Supported platforms include ESSL and FreeFlow.

    Useful Queries: 
    1. Query: "SELECT hit_rate FROM cache_efficiency WHERE hit_rate < 90" | Description: "Find low cache hit rates"
    2. Query: "SELECT bypass_reason, COUNT(*) FROM cache_efficiency GROUP BY bypass_reason" | Description: "Aggregate bypass reasons"

    Columns:
    Name: cache_group_id | Type: int | Description: Unique identifier for the edge group
    Name: hit_rate | Type: float | Description: Percentage of requests hit
    Name: bypass_reason | Type: string | Description: Why a request skipped cache, if applicable
    """
   
    # Set a unique thread ID for this specific run so LangGraph can track memory
    config = {"configurable": {"thread_id": "jira-ticket-test-001"}}

    # 1. Start the graph (It will run Extract -> Render, then PAUSE before Validate)
    initial_inputs = {"jira_ticket_text": ticket_payload}
    for event in app.stream(initial_inputs, config):
        pass # Stream output omitted for brevity
        
    print("\nGRAPH PAUSED: Ready for Human Review.")
    
    # 2. Get the current state to show the human
    current_state = app.get_state(config)
    staged_xml = current_state.values.get("xml_string")
    
    if not current_state.values.get("error"):
        print("\n--- STAGED XML ---")
        print(staged_xml)
        print("------------------\n")
        
        # --- HUMAN INTERVENTION HAPPENS HERE ---
        print("[Human] Making final edits and approving...")
        # Simulate an edit (e.g., human fixes a description)
        edited_xml = staged_xml 
        
        # 3. Inject the edited XML back into the state
        # Using as_node="render_xml" tricks LangGraph into thinking the render node 
        # just finished, so it knows to step into validate_xml next.
        app.update_state(config, {"xml_string": edited_xml}, as_node="render_xml")
        
        # 4. Resume the graph (It will run Validate -> Save)
        print("\nRESUMING GRAPH: Validating and saving...")
        for event in app.stream(None, config):
            pass
            
        # 5. Check the final outcome
        final_state = app.get_state(config)
        if final_state.values.get("error"):
            # If the human introduced a broken XML tag, the validate node catches it here!
            print(f"\nExecution stopped! The edited XML failed validation:\n{final_state.values.get('error')}")
        else:
            print(f"\nSuccess! Final XML Output File Generated: {final_state.values.get('xml_filename')}")
    else:
        print(f"\nExecution stopped due to earlier error: {current_state.values.get('error')}")
