import gradio as gr
import sys
import os

# 1. Setup paths
root_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(root_dir, 'Project'))
sys.path.append(os.path.join(root_dir, 'GhostH3'))

# 2. Import the UIs
from LogFormat.app import ui as log_format_ui
from QueryTable.app import ui as query_table_ui

# 3. Use TabbedInterface to safely merge the pre-built Blocks
unified_app = gr.TabbedInterface(
    interface_list=[log_format_ui, query_table_ui],
    tab_names=["Log-Format Updation", "Query Table Updation"],
    title="Ghost Documentation Update Agent"
)

if __name__ == "__main__":
    # Pass the theme into the launch method to avoid Gradio 6.0 warnings
    unified_app.launch(
        server_port=7860, 
        theme=gr.themes.Ocean()
    )
