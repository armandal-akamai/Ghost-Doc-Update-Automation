import gradio as gr
from main import app as langgraph_app

# --- EVENT HANDLERS ---

def start_extraction(ticket_id: str, ticket_text: str):
    """Kicks off the graph, extracts data, renders XML, and pauses for review."""
    if not ticket_id or not ticket_text:
        gr.Warning("Please provide both a Ticket ID and the Jira Ticket Text.")
        yield (
            gr.update(value="Status: ⚪ Standby"),
            gr.update(visible=False), gr.update(),
            gr.update(visible=False), gr.update()
        )
        return
        
    config = {"configurable": {"thread_id": ticket_id.strip()}}
    
    # 1. IMMEDIATE UI UPDATE: Show loading state
    yield (
        gr.update(value="Status: ⏳ AI is processing Jira ticket and rendering XML..."),
        gr.update(visible=False), gr.update(), 
        gr.update(visible=False), gr.update()  
    )
    
    try:
        # 2. RUN GRAPH: Stream until the interrupt before "validate_xml"
        initial_inputs = {"jira_ticket_text": ticket_text}
        for _ in langgraph_app.stream(initial_inputs, config):
            pass
            
        # 3. FETCH STATE: Grab the staged XML string
        current_state = langgraph_app.get_state(config)
        
        if current_state.values.get("error"):
            # Graph hit an error during extraction/rendering
            yield (
                gr.update(value="Status: 🔴 Extraction Failed"),
                gr.update(visible=False), gr.update(), 
                gr.update(visible=True), gr.update(value=f"❌ Error:\n{current_state.values.get('error')}")
            )
            return

        staged_xml = current_state.values.get("xml_string", "")
        
        # 4. FINAL UI UPDATE: Present the XML editor to the human (5 outputs)
        yield (
            gr.update(value="Status: 🔵 Ready for Review"),
            gr.update(visible=True), gr.update(value=staged_xml), 
            gr.update(visible=False), gr.update()                 
        )
        
    except Exception as e:
        yield (
            gr.update(value="Status: 🔴 System Error"),
            gr.update(visible=False), gr.update(), 
            gr.update(visible=True), gr.update(value=f"### ⚠️ Crash:\n```text\n{str(e)}\n```")
        )

def approve_and_save(ticket_id: str, edited_xml: str):
    """Injects the human-reviewed XML back into state and resumes validation & saving."""
    config = {"configurable": {"thread_id": ticket_id.strip()}}
    
    # 1. IMMEDIATE UI UPDATE: Show loading state (4 outputs)
    yield (
        gr.update(value="Status: 🚀 Validating & Saving..."),
        gr.update(visible=False),                     
        gr.update(visible=False), gr.update(value="") 
    )
    
    try:
        # 2. INJECT EDITS: Update state and trick LangGraph
        langgraph_app.update_state(config, {"xml_string": edited_xml}, as_node="render_xml")
        
        # 3. RESUME GRAPH: Runs validate_xml -> save_xml
        for _ in langgraph_app.stream(None, config):
            pass
            
        # 4. EVALUATE OUTCOME
        final_state = langgraph_app.get_state(config)
        error = final_state.values.get("error")
        
        if error:
            # Validation Failed (e.g., bad XML tag)
            yield (
                gr.update(value="Status: 🔴 Validation Failed"),
                gr.update(visible=True),   
                gr.update(visible=True), gr.update(value=f"❌ xmllint Error:\n{error}")
            )
        else:
            # Success!
            filename = final_state.values.get("xml_filename")
            success_msg = f"✅ Success! XML passed validation and was saved to disk as: **{filename}**"
            yield (
                gr.update(value="Status: 🟢 Saved Successfully"),
                gr.update(visible=False),  
                gr.update(visible=True), gr.update(value=success_msg) 
            )
            
    except Exception as e:
        yield (
            gr.update(value="Status: 🔴 Execution Error"),
            gr.update(visible=True),       
            gr.update(visible=True), gr.update(value=f"❌ Error:\n{str(e)}")
        )

# --- UI LAYOUT DESIGN ---

with gr.Blocks() as ui:
    gr.Markdown("# Query Table Updater")
    status_indicator = gr.Markdown("Status: ⚪ Standby")
    
    # 1. THE PERSISTENT CONTROL BAR
    with gr.Row():
        ticket_input = gr.Textbox(label="Jira Ticket ID (Thread ID)", placeholder="MDR-123", scale=1)
        desc_input = gr.Textbox(label="Paste Jira Description Here", lines=4, scale=3)
        fetch_btn = gr.Button("🔍 Extract & Render", variant="primary", scale=1)
        
    gr.Markdown("---")
    
    # 2. STATE A: REVIEW & EDIT XML (Hidden by default)
    with gr.Column(visible=False) as review_col:
        gr.Markdown("### Review Generated XML")
        xml_editor = gr.Code(
            label="Staged XML (Editable)", 
            language="html", 
            interactive=True,
            lines=20
        )
        approve_btn = gr.Button("✅ Approve, Validate & Save", variant="stop")
        
    # 3. STATE B: LOGS & ERRORS (Hidden by default)
    with gr.Column(visible=False) as log_col:
        output_log = gr.Markdown(label="System Output")

    # --- WIRING THE BUTTONS TO THE EVENTS ---
    
    # Clicking "Extract & Render" triggers Node 1 & 2 (5 outputs)
    fetch_btn.click(
        fn=start_extraction, 
        inputs=[ticket_input, desc_input], 
        outputs=[status_indicator, review_col, xml_editor, log_col, output_log]
    )
    
    # Clicking "Approve" triggers Validation & Node 3 (4 outputs)
    approve_btn.click(
        fn=approve_and_save,
        inputs=[ticket_input, xml_editor],
        outputs=[status_indicator, review_col, log_col, output_log]
    )

if __name__ == "__main__":
    ui.launch(server_port=7860)
