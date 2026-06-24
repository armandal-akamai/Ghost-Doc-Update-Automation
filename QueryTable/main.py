"""
This module defines, compiles, and executes a stateful LangGraph automation 
pipeline. It parses raw database schema definitions from developer Jira tickets, 
transforms them into structured Pydantic objects, and utilizes Perforce to dynamically 
build or merge Akamai MDR XML tables. It runs validation diagnostics and orchestrates a 
human-in-the-loop authorization checkpoint before deploying changes to the server.

Workflow Node Architecture: 
    1. Extract (`extract_jira_node`): Passes the text entered by the user to an LLM, which produces a structured MDRQueryTable Pydantic model.
    2. Build (`perforce_builder_node`): Queries Perforce to determine table existence. If the table is new, it renders a fresh XML using a Jinja2 template. If it exists, it safely deep-merges the new columns/queries into the existing file.
    3. Validate (`validate_xml_node`): Validates the in-memory XML string using the xmllint binary.
    4. Deploy (`perforce_deploy_node`): Uses a FileLock to safely write the finalized, human-approved XML to the local workspace and automatically submits it to the Perforce depot.

State Management & Human Intervention:
    The application utilizes an in-memory `MemorySaver` checkpointer. The graph 
    is compiled with a hard pause flag: `interrupt_before=["validate_xml"]`. 
    This suspends execution right after the XML is built (but before validation) 
    so that the final payload can be reviewed and manually edited by the user in the UI.
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

from P4 import P4, P4Exception
from filelock import FileLock
from QueryTable.xmlMerger import merge_query_table_update

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
    p4_action: Optional[str]

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

def perforce_builder_node(state: GraphState) -> dict:
    if state.get("error"): return state
    
    table_data = state["table_data"]
    table_name = table_data.name
    depot_path = f"//sandbox/armandal/QueryTable/{table_name}.xml"
    # Match the local path to your workspace view!
    local_path = os.path.join(ROOT_DIR, "QueryTable", "tempFolder", f"{table_name}.xml")
    
    p4 = P4()
    p4.port = os.getenv("P4PORT")
    p4.user = os.getenv("P4USER")
    p4.client = os.getenv("P4CLIENT")
    p4.exception_level = 1
    p4.connect()
    
    try:
        # Check if file exists in depot
        try:
            files_result = p4.run_files(depot_path)
            is_update = len(files_result) > 0
        except P4Exception:
            is_update = False
            
        if is_update:
            print(f"[Node: Builder] Table exists. Syncing and merging...")
            p4.run_sync("-f", depot_path)
            with open(local_path, "r", encoding="utf-8") as f:
                existing_xml = f.read()
            final_xml = merge_query_table_update(existing_xml, table_data)
            action = "edit"
        else:
            print(f"[Node: Builder] New table. Rendering Jinja template...")
            # Run your existing Jinja2 logic here
            env = Environment(loader=FileSystemLoader(BASE_DIR), trim_blocks=True, lstrip_blocks=True)
            template = env.get_template('table_template.xml')
            final_xml = template.render(table=table_data)
            action = "add"
            
        return {"xml_string": final_xml, "p4_action": action}
        
    except Exception as e:
        return {"error": f"Perforce/Builder Error: {str(e)}"}
    finally:
        p4.disconnect()

def perforce_deploy_node(state: GraphState, config: RunnableConfig) -> dict:
    if state.get("error"): return state
    
    table_name = state["table_data"].name
    local_path = os.path.join(ROOT_DIR, "QueryTable", "tempFolder", f"{table_name}.xml")
    lock_path = os.path.join(ROOT_DIR, "querytable_deploy.lock")
    
    lock = FileLock(lock_path, timeout=60)
    
    with lock:
        p4 = P4()
        p4.port = os.getenv("P4PORT")
        p4.user = os.getenv("P4USER")
        p4.client = os.getenv("P4CLIENT")
        p4.connect()
        
        try:
            # 1. Prepare Perforce for the file change
            if state["p4_action"] == "edit":
                p4.run_edit(local_path) # Must run edit BEFORE writing so OS unlocks the file
                
            # 2. Write the human-approved XML to disk
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(state["xml_string"])
                
            # 3. If adding, we add AFTER the file physically exists on disk
            if state["p4_action"] == "add":
                p4.run_add(local_path)
                
            # 4. Submit
            commit_msg = f"Automated QueryTable update for {table_name}"
            p4.run_submit("-d", commit_msg, local_path)
            print(f"[Node: Deploy] SUCCESS! Submitted to Perforce.")
            return {"xml_filename": local_path}
            
        except P4Exception as e:
            p4.run_revert(local_path)
            return {"error": f"Submit failed: {str(e)}"}
        finally:
            p4.disconnect()

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
    return "perforce_deploy"

# 4. Build and Compile the Graph Workflow
workflow = StateGraph(GraphState)

# Add our processing blocks
workflow.add_node("extract_jira", extract_jira_node)
workflow.add_node("perforce_builder", perforce_builder_node)
workflow.add_node("perforce_deploy", perforce_deploy_node)
workflow.add_node("validate_xml", validate_xml_node)

# Set up the linear execution route
workflow.add_edge(START, "extract_jira")
workflow.add_edge("extract_jira", "perforce_builder")
workflow.add_edge("perforce_builder", "validate_xml")
workflow.add_conditional_edges(
    "validate_xml",             # The node we are coming from
    route_after_validation,     # The routing logic
    {
        "validate_xml": "validate_xml", # If router says 'validate_xml', loop back
        "perforce_deploy": "perforce_deploy"          # If router says 'save_xml', move forward
    }
)
workflow.add_edge("perforce_deploy", END)

# Compile into an executable application
memory = MemorySaver()
app = workflow.compile(checkpointer=memory, interrupt_before=["validate_xml"])
