import xml.etree.ElementTree as ET

def merge_query_table_update(existing_xml_string: str, update_payload) -> str:
    """
    Safely merges new columns and queries into an existing QueryTable XML string
    while properly handling embedded HTML nodes and protecting existing data types.
    """
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(existing_xml_string, parser=parser)

    # 1. PROTECT ROOT ATTRIBUTES: Only overwrite if it isn't a generic fallback
    if getattr(update_payload, "publisher", None):
        root.set("publisher", update_payload.publisher)
        
    if getattr(update_payload, "owner", None):
        # Only overwrite owner if the LLM extracted a real address, not the fallback domain
        if "default" not in update_payload.owner and "@domain.com" not in update_payload.owner:
            root.set("owner", update_payload.owner)
        
    # 2. UPDATE OR APPEND COLUMNS
    if getattr(update_payload, "columns", None):
        columns_node = root.find("Columns")
        if columns_node is None:
            columns_node = ET.Element("Columns")
            root.insert(0, columns_node)
            
        existing_cols = {col.findtext("ColumnName"): col for col in columns_node.findall("Column")}
        
        for new_col in update_payload.columns:
            if not new_col.name:
                continue
                
            if new_col.name in existing_cols:
                col_node = existing_cols[new_col.name]
                
                # FIX 1: Don't let a default 'string' fallback overwrite a specialized type like 'll'
                current_type = col_node.findtext("ColumnType")
                if new_col.type and (new_col.type != "string" or not current_type):
                    col_node.find("ColumnType").text = new_col.type
                
                # FIX 2: Clear old text/nodes and parse HTML tags cleanly without escaping them
                desc_node = col_node.find("ColumnDesc")
                if desc_node is not None and new_col.description:
                    desc_node.clear()  # Wipes out all old inner text and ghost child tags perfectly!
                    try:
                        # Wrap in a temporary tag to parse the valid HTML string safely
                        html_fragment = ET.fromstring(f"<div>{new_col.description}</div>")
                        for child in html_fragment:
                            desc_node.append(child)
                    except ET.ParseError:
                        desc_node.text = new_col.description
            else:
                # INJECT: Brand new column structure handles HTML cleanly as well
                col_node = ET.SubElement(columns_node, "Column")
                ET.SubElement(col_node, "ColumnType").text = new_col.type or "string"
                
                desc_node = ET.SubElement(col_node, "ColumnDesc")
                if new_col.description:
                    try:
                        html_fragment = ET.fromstring(f"<div>{new_col.description}</div>")
                        for child in html_fragment:
                            desc_node.append(child)
                    except ET.ParseError:
                        desc_node.text = new_col.description
                        
                ET.SubElement(col_node, "ColumnName").text = new_col.name

    # 3. APPEND USEFUL QUERIES (With clean HTML formatting for QueryDesc)
    if getattr(update_payload, "useful_queries", None):
        queries_node = root.find("UsefulQueries")
        if queries_node is None:
            queries_node = ET.Element("UsefulQueries")
            root.append(queries_node)
            
        for new_query in update_payload.useful_queries:
            q_node = ET.SubElement(queries_node, "Query")
            ET.SubElement(q_node, "QueryToSend").text = new_query.query_to_send
            
            q_desc_node = ET.SubElement(q_node, "QueryDesc")
            if new_query.query_desc:
                try:
                    html_fragment = ET.fromstring(f"<div>{new_query.query_desc}</div>")
                    for child in html_fragment:
                        q_desc_node.append(child)
                except ET.ParseError:
                    q_desc_node.text = new_query.query_desc
                    
            ET.SubElement(q_node, "QueryResult").text = "\n" + new_query.query_result + "\n      "

    # 4. PRETTIFY TREE
    ET.indent(root, space="  ", level=0)
    
    return ET.tostring(root, encoding="utf-8").decode("utf-8")
