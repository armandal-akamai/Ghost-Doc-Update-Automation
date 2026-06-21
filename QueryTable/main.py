"""
This module defines, compiles, and executes a stateful LangGraph automation 
pipeline. It parses raw database schema definitions from developer Jira tickets, 
transforms them into structured Pydantic objects, renders them into Akamai MDR XML 
tables using Jinja2, runs validation diagnostics, and orchestrates a 
human-in-the-loop authorization checkpoint.

Workflow node Architecture: 
    1. Extract(`extract_jira_node`) : This node passes the the text entered by the user to an LLM which produces a structured MDRQueryTable Pydantic model.
    2. Render(`render_xml_node`) : This takes the Pydantic data and converts it into the xml using the Jinja2 template and stores it in memory.
    3. Validate(`validate_xml_node`): This nodes validate the in-memory XML using xmllint binary.
    4. Save(`save_xml_node`): Writes the finalized, approved XML payload into an isolated sandbox subdirectory inside `agent_workspaces/` tracked by `thread_id`.

State Management & Human Intervention:
    The application utilizes an in-memory `MemorySaver` checkpointer. The graph 
    is compiled with a hard pause flag: `interrupt_before=["validate_xml"]`. 
    This suspends execution right after the XML is rendered so that it can be edited by the user before it passes through the validation block.
"""

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
from QueryTable.pyd import MDRQueryTable 
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
    print("[Node: Extract] AI Agent is processing Jira ticket ...")
    with open(os.path.join(BASE_DIR, "systemPrompt.txt"), "r") as f:
        system_prompt = f.read()
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

def route_after_validation(state: GraphState) -> str:
    """Checks if validation passed. If it failed, loop back for human review."""
    if state.get("error"):
        # Loop back, Because interrupt_before=["validate_xml"] is set, 
        # the graph will immediately pause again so the user can fix it.
        return "validate_xml" 
    
    # If no error, proceed to save
    return "save_xml"

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
workflow.add_conditional_edges(
    "validate_xml",             # The node we are coming from
    route_after_validation,     # The routing logic
    {
        "validate_xml": "validate_xml", # If router says 'validate_xml', loop back
        "save_xml": "save_xml"          # If router says 'save_xml', move forward
    }
)
workflow.add_edge("save_xml", END)

# Compile into an executable application
memory = MemorySaver()
app = workflow.compile(checkpointer=memory, interrupt_before=["validate_xml"])
