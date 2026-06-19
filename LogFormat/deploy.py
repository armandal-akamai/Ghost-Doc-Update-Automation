import xml.etree.ElementTree as ET
import yaml
import json
import re
from xmlGenerator import UniversalXMLGenerationEngine
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def merge_nodes(target, source, level=1):
    """
    Recursively deep merges a source XML node into a target XML node.
    Only formats newly appended nodes, preserving the formatting of existing nodes.
    """
    # 1. Merge attributes (Update existing, add new)
    for k, v in source.attrib.items():
        target.set(k, v)
    
    # 2. Merge text if present
    if source.text and source.text.strip():
        target.text = source.text
        
    # 3. Merge children
    for source_child in list(source):
        # Special handling for <doc>: We always replace the old doc with the new one
        if source_child.tag == "doc":
            old_doc = target.find("doc")
            if old_doc is not None:
                # Preserve the exact whitespace of the old doc so we don't mess up surrounding spacing
                source_child.tail = old_doc.tail
                target.remove(old_doc)
            else:
                # New doc insertion
                source_child.tail = "\n" + ("  " * level)
                if not target.text or not target.text.strip():
                    target.text = "\n" + ("  " * (level + 1))
            
            # Format only the interior of the new doc
            ET.indent(source_child, space="  ", level=level + 1)
            target.insert(0, source_child)
            continue

        # Determine how to match this child against existing children
        match = None
        if "id" in source_child.attrib:
            match = target.find(f"./{source_child.tag}[@id='{source_child.attrib['id']}']")

            # 2. If it's a log-field hidden inside a historical group container, tunnel into the group
            if match is None:
                for container in list(target):
                    if container.tag != source_child.tag:  # Avoid deep-tunneling into nested fields of the same type
                        match = container.find(f"./{source_child.tag}[@id='{source_child.attrib['id']}']")
                        if match is not None:
                            break
        elif "since" in source_child.attrib:
            match = target.find(f"./{source_child.tag}[@since='{source_child.attrib['since']}']")
        elif "pattern" in source_child.attrib:
            match = target.find(f"./{source_child.tag}[@pattern='{source_child.attrib['pattern']}']")
        else:
            # Added "status-char" to repeatable tags to bulletproof edge cases
            repeatable_tags = ["enum", "bitflag", "bitfield", "bitval", "sub-char", "status-char", "change"] 
            if source_child.tag not in repeatable_tags:
                match = target.find(f"./{source_child.tag}")
        
        if match is not None:
            # Recurse deeper, increasing the depth level
            merge_nodes(match, source_child, level + 1)
        else:
            # BRAND NEW ELEMENT - SURGICAL INSERTION
            
            # 1. Prettify this newly created branch
            ET.indent(source_child, space="  ", level=level + 1)
            
            # 2. Fix the insertion point in the target so it drops to a new line cleanly
            indent_space = "\n" + ("  " * (level + 1))
            if len(target) > 0:
                # Target has existing children, put newline after the last one
                target[-1].tail = indent_space
            else:
                # Target was empty, put newline before the first child
                if not target.text or not target.text.strip():
                    target.text = indent_space
            
            # 3. Close the parent tag correctly
            source_child.tail = "\n" + ("  " * level)
            
            target.append(source_child)

def deploy_to_master(yaml_file, schema_file, master_file, output_file):
    """Compiles YAML and performs an intelligent, non-destructive merge/upsert on the master file."""
    
    with open(schema_file, "r", encoding="utf-8") as sf:
        schema = json.load(sf)
    with open(yaml_file, "r", encoding="utf-8") as yf:
        yaml_data = yaml.safe_load(yf)

    print(f"[INFO] Step 1: Compiling '{yaml_file}' into XML fragment...")
    engine = UniversalXMLGenerationEngine(yaml_data, schema)
    fragment = engine.generate_xml()
    action_type = yaml_data.get("action_type")

    print(f"[INFO] Step 2: Parsing master file '{master_file}'...")
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    tree = ET.parse(master_file, parser=parser)
    root = tree.getroot()

    print(f"[INFO] Step 3: Performing surgical injection (Action: {action_type})...")
    if action_type == "append_changelog":
        changelog_node = root.find(".//changelog")
        if changelog_node is not None:
            ET.indent(fragment, space="  ", level=2)
            fragment.tail = "\n  "
            if len(changelog_node) > 0:
                changelog_node[-1].tail = "\n    "
            changelog_node.append(fragment)
            print("       [SUCCESS] Appended new <change> entry to <changelog>.")
        else:
            print("       [ERROR] Could not find <changelog> tag in master file.")

    elif action_type == "update_logline":
        fragment_id = fragment.get("id")
        existing_node = root.find(f".//log-line[@id='{fragment_id}']")
        
        if existing_node is not None:
            print(f"       [INFO] Log-line id='{fragment_id}' matches an existing node. Performing deep merge...")
            # Start merging at level 1 (since <log-line> is indented 1 level deep in <log-format>)
            merge_nodes(existing_node, fragment, level=1)
            print(f"       [SUCCESS] Smart deep merge complete for <log-line id='{fragment_id}'>.")
        else:
            ET.indent(fragment, space="  ", level=1)
            fragment.tail = "\n"
            
            # 1. Find all existing log-lines in the root
            existing_log_lines = root.findall("log-line")
            
            if existing_log_lines:
                # 2. Get the very last log-line currently in the file
                last_log_line = existing_log_lines[-1]
                
                # 3. Find its exact index position in the document
                insert_index = list(root).index(last_log_line) + 1
                
                # 4. Fix the tail spacing of the old last line so our new one drops cleanly
                last_log_line.tail = "\n  "
                
                # 5. Insert the new fragment at that specific index
                root.insert(insert_index, fragment)
            else:
                # Fallback: If for some reason there are ZERO log lines in the file, just append
                if len(root) > 0:
                    root[-1].tail = "\n  "
                root.append(fragment)
                
            print(f"       [SUCCESS] Inserted brand new <log-line id='{fragment_id}'>.")

    print(f"[INFO] Step 4: Deployment complete! Changes saved to '{output_file}'.")
    
    xml_str = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    
    if "<!DOCTYPE" not in xml_str:
        xml_str = re.sub(r'(<\?xml[^>]+\?>\s*)', r'\1<!DOCTYPE log-format SYSTEM "log-format.dtd">\n', xml_str, count=1)
    # Safely restore the XML Stylesheet line right below the DOCTYPE if it was stripped

    if "<?xml-stylesheet" not in xml_str:
        xml_str = xml_str.replace(
            '<!DOCTYPE log-format SYSTEM "log-format.dtd">',
            '<!DOCTYPE log-format SYSTEM "log-format.dtd">\n<?xml-stylesheet href="log-format.xsl" type="text/xsl"?>'
        )

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

if __name__ == "__main__":
    deploy_to_master(
        yaml_file=os.path.join(BASE_DIR, "input.yaml"), 
        schema_file=os.path.join(BASE_DIR, "schema_map.json"), 
        master_file=os.path.join(BASE_DIR, "log-format.xml"), 
        output_file=os.path.join(BASE_DIR, "log-format-updated.xml")
    )
