import json
import yaml
import xml.etree.ElementTree as ET
from xml.dom import minidom
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class UniversalXMLGenerationEngine:
    def __init__(self, yaml_data, schema_map):
        self.yaml_data = yaml_data
        self.schema_map = schema_map
        self.bp = schema_map.get("documentation_blueprint", {})

    def _apply_attributes_from_schema(self, element, source_data, schema_node):
        """Maps YAML keys to XML attributes based on the JSON blueprint rules"""
        attr_schema = schema_node.get("attributes", {})
        for yaml_key, attr_meta in attr_schema.items():
            if yaml_key in source_data:
                xml_attr_name = attr_meta.get("xml_name", yaml_key)
                val = source_data[yaml_key]
                val_str = str(val).lower() if isinstance(val, bool) else str(val)
                element.set(xml_attr_name, val_str)

    def _build_doc_tag(self, parent_element, data_dict):
        """Upgraded to support human-written HTML markup and strip duplicate blank lines"""
        if "doc" in data_dict and data_dict["doc"]:
            try:
                rich_doc_element = ET.fromstring(f"<doc>{data_dict['doc'].strip()}</doc>")
                def clean_empty_whitespace(elem):
                    if elem.text and not elem.text.strip():
                        elem.text = None
                    if elem.tail and not elem.tail.strip():
                        elem.tail = None
                    for child in list(elem):
                        clean_empty_whitespace(child)
                clean_empty_whitespace(rich_doc_element)
                parent_element.append(rich_doc_element)
            except ET.ParseError:
                doc_node = ET.SubElement(parent_element, "doc")
                doc_node.text = str(data_dict["doc"])

    def _build_parse_info(self, parent_element, data_dict):
        if "parse_info" in data_dict:
            pi_schema = self.bp["advanced_substructures"]["parse_info"]
            pi_node = ET.SubElement(parent_element, pi_schema["xml_element"])
            self._apply_attributes_from_schema(pi_node, data_dict["parse_info"], pi_schema)

    def _get_shared_schema(self, component_name):
        """Borrows the schema definition from where it currently lives in the JSON map."""
        return self.bp["advanced_substructures"]["sub_fields"]["child_sections"]["sub_field_items"]["child_sections"][component_name]

    def _build_enums(self, parent_node, data_dict):
        if "enums" in data_dict:
            schema = self._get_shared_schema("enums")
            root = ET.SubElement(parent_node, schema["xml_element"])
            item_schema = schema["child_sections"]["enum_items"]
            for item in data_dict.get("enums", []):
                node = ET.SubElement(root, item_schema["xml_element"])
                self._apply_attributes_from_schema(node, item, item_schema)
                self._build_doc_tag(node, item)



    def _build_bitmask(self, parent_node, data_dict):
        if "bitmask" in data_dict:
            schema = self._get_shared_schema("bitmask")
            root = ET.SubElement(parent_node, schema["xml_element"])
            
            if "bitflag_items" in schema["child_sections"]:
                bf_item_schema = schema["child_sections"]["bitflag_items"]
                for flag in data_dict["bitmask"].get("bitflag_items", []):
                    node = ET.SubElement(root, bf_item_schema["xml_element"])
                    self._apply_attributes_from_schema(node, flag, bf_item_schema)
                    self._build_doc_tag(node, flag)
                    
            if "bitfield_items" in schema["child_sections"]:
                bfield_item_schema = schema["child_sections"]["bitfield_items"]
                for bfield in data_dict["bitmask"].get("bitfield_items", []):
                    node = ET.SubElement(root, bfield_item_schema["xml_element"])
                    self._apply_attributes_from_schema(node, bfield, bfield_item_schema)
                    self._build_doc_tag(node, bfield)
                    
                    # New logic to handle nested <bitval> elements
                    if "bitval_items" in bfield_item_schema["child_sections"]:
                        bval_schema = bfield_item_schema["child_sections"]["bitval_items"]
                        for bval in bfield.get("bitval_items", []):
                            bval_node = ET.SubElement(node, bval_schema["xml_element"])
                            self._apply_attributes_from_schema(bval_node, bval, bval_schema)
                            self._build_doc_tag(bval_node, bval)

    def _build_sub_chars(self, parent_node, data_dict):
        if "sub_chars" in data_dict:
            schema = self._get_shared_schema("sub_chars")
            root = ET.SubElement(parent_node, schema["xml_element"])
            item_schema = schema["child_sections"]["sub_char_items"]
            for item in data_dict["sub_chars"].get("sub_char_items", []):
                node = ET.SubElement(root, item_schema["xml_element"])
                self._apply_attributes_from_schema(node, item, item_schema)
                self._build_doc_tag(node, item)

    def process_status_chars(self, parent_node, sc_yaml):
        sc_schema = self.bp["advanced_substructures"]["status_chars"]
        sc_root = ET.SubElement(parent_node, sc_schema["xml_element"])
        self._apply_attributes_from_schema(sc_root, sc_yaml, sc_schema)

        if "status_char_groups" in sc_schema["child_sections"]:
            group_schema = sc_schema["child_sections"]["status_char_groups"]
            item_schema = group_schema["child_sections"]["status_char_items"]
            for group in sc_yaml.get("status_char_groups", []):
                group_node = ET.SubElement(sc_root, group_schema["xml_element"])
                self._apply_attributes_from_schema(group_node, group, group_schema)
                self._build_doc_tag(group_node, group)
                for item in group.get("status_char_items", []):
                    item_node = ET.SubElement(group_node, item_schema["xml_element"])
                    self._apply_attributes_from_schema(item_node, item, item_schema)
                    self._build_doc_tag(item_node, item)

        if "status_char_items" in sc_schema["child_sections"]:
            loose_item_schema = sc_schema["child_sections"]["status_char_items"]
            for item in sc_yaml.get("status_char_items", []):
                item_node = ET.SubElement(sc_root, loose_item_schema["xml_element"])
                self._apply_attributes_from_schema(item_node, item, loose_item_schema)
                self._build_doc_tag(item_node, item)

    def process_sub_fields(self, parent_node, sub_fields_yaml):
        sf_schema = self.bp["advanced_substructures"]["sub_fields"]
        sub_fields_root = ET.SubElement(parent_node, sf_schema["xml_element"])
        
        if "splitter" in sub_fields_yaml and "splitter" in sf_schema["child_sections"]:
            splitter_meta = sf_schema["child_sections"]["splitter"]
            split_node = ET.SubElement(sub_fields_root, splitter_meta["xml_element"])
            self._apply_attributes_from_schema(split_node, sub_fields_yaml["splitter"], splitter_meta)
            
        if "sub_field_items" in sf_schema["child_sections"]:
            item_schema = sf_schema["child_sections"]["sub_field_items"]
            for item in sub_fields_yaml.get("sub_field_items", []):
                sf_node = ET.SubElement(sub_fields_root, item_schema["xml_element"])
                self._apply_attributes_from_schema(sf_node, item, item_schema)
                self._build_parse_info(sf_node, item)
                self._build_doc_tag(sf_node, item)

                self._build_enums(sf_node, item)
                self._build_bitmask(sf_node, item)
                self._build_sub_chars(sf_node, item)

                if "sub_fields" in item:
                    self.process_sub_fields(sf_node, item["sub_fields"])
                if "named_sub_fields" in item:
                    self.process_named_sub_fields(sf_node, item["named_sub_fields"])

    def process_named_sub_fields(self, parent_node, named_yaml):
        nsf_schema = self.bp["advanced_substructures"]["named_sub_fields"]
        named_root = ET.SubElement(parent_node, nsf_schema["xml_element"])
        
        if "splitter" in named_yaml:
            split_meta = nsf_schema["child_sections"]["splitter"]
            s_node = ET.SubElement(named_root, split_meta["xml_element"])
            self._apply_attributes_from_schema(s_node, named_yaml["splitter"], split_meta)
            
        if "name_value_splitter" in named_yaml:
            nvs_meta = nsf_schema["child_sections"]["name_value_splitter"]
            nvs_node = ET.SubElement(named_root, nvs_meta["xml_element"])
            self._apply_attributes_from_schema(nvs_node, named_yaml["name_value_splitter"], nvs_meta)
            
        field_schema = nsf_schema["child_sections"]["named_fields"]
        for field in named_yaml.get("named_fields", []):
            nf_node = ET.SubElement(named_root, field_schema["xml_element"])
            self._apply_attributes_from_schema(nf_node, field, field_schema)
            self._build_parse_info(nf_node, field)
            self._build_doc_tag(nf_node, field)
            
            self._build_enums(nf_node, field)
            self._build_bitmask(nf_node, field)
            self._build_sub_chars(nf_node, field)

            if "status_chars" in field:
                self.process_status_chars(nf_node, field["status_chars"])
            if "sub_fields" in field:
                self.process_sub_fields(nf_node, field["sub_fields"])
            if "named_sub_fields" in field:
                self.process_named_sub_fields(nf_node, field["named_sub_fields"])

    def process_single_field(self, parent_node, field_yaml, field_schema):
        """Processes an individual <log-field> mapping"""
        field_node = ET.SubElement(parent_node, field_schema["xml_element"])
        self._apply_attributes_from_schema(field_node, field_yaml, field_schema)
        self._build_parse_info(field_node, field_yaml)
        self._build_doc_tag(field_node, field_yaml)
        
        self._build_enums(field_node, field_yaml)
        self._build_bitmask(field_node, field_yaml)
        self._build_sub_chars(field_node, field_yaml)
        
        if "status_chars" in field_yaml:
            self.process_status_chars(field_node, field_yaml["status_chars"])
        if "named_sub_fields" in field_yaml:
            self.process_named_sub_fields(field_node, field_yaml["named_sub_fields"])
        elif "sub_fields" in field_yaml:
            self.process_sub_fields(field_node, field_yaml["sub_fields"])

    def generate_xml(self):
        action_type = self.yaml_data.get("action_type")
        
        if action_type == "append_changelog":
            cl_schema = self.bp["changelog_route"]
            root_element = ET.Element(cl_schema["xml_element"])
            self._apply_attributes_from_schema(root_element, self.yaml_data["changelog_entry"], cl_schema)
            
            if "change_summary" in self.yaml_data["changelog_entry"]:
                root_element.text = self.yaml_data["changelog_entry"]["change_summary"]
            return root_element

        elif action_type == "update_logline":
            logline_schema = self.bp["logline_route"]
            logline_data = self.yaml_data["log-line"]
            root_element = ET.Element(logline_schema["xml_element"])
            self._apply_attributes_from_schema(root_element, logline_data, logline_schema)
            self._build_doc_tag(root_element, logline_data)
            
            parent_for_fields_list = []
            
            if "log_line_versions" in logline_data:
                llv_schema = logline_schema["child_sections"]["log_line_version"]
                for version_data in logline_data["log_line_versions"]:
                    llv_node = ET.SubElement(root_element, llv_schema["xml_element"])
                    self._apply_attributes_from_schema(llv_node, version_data, llv_schema)
                    self._build_doc_tag(llv_node, version_data)
                    parent_for_fields_list.append((llv_node, version_data))
            else:
                parent_for_fields_list = [(root_element, logline_data.get("log_fields", {}))]

            group_schema = self.bp["fields_routing"].get("log_field_groups", {})
            field_schema = self.bp["fields_routing"]["standalone_fields"]

            for parent_node, fields_section in parent_for_fields_list:
                for group in fields_section.get("log_field_groups", []):
                    group_node = ET.SubElement(parent_node, group_schema["xml_element"])
                    self._apply_attributes_from_schema(group_node, group, group_schema)
                    self._build_doc_tag(group_node, group)
                    
                    for field in group.get("standalone_fields", []):
                        self.process_single_field(group_node, field, field_schema)

                for field in fields_section.get("standalone_fields", []):
                    self.process_single_field(parent_node, field, field_schema)
                
            return root_element
            
        else:
            raise NotImplementedError(f"Action type '{action_type}' is not supported.")

if __name__ == "__main__":
    try:
        with open(os.path.join(BASE_DIR, "schema_map.json"), "r", encoding="utf-8") as json_file:
            loaded_schema = json.load(json_file)
            
        with open(os.path.join(BASE_DIR, "input.yaml"), "r", encoding="utf-8") as yaml_file:
            loaded_yaml = yaml.safe_load(yaml_file)

        print("Compiling advanced YAML layout into XML based on blueprint rules...")
        engine = UniversalXMLGenerationEngine(loaded_yaml, loaded_schema)
        xml_tree_root = engine.generate_xml()
        
        raw_xml_str = ET.tostring(xml_tree_root, encoding="utf-8")
        pretty_xml_str = minidom.parseString(raw_xml_str).toprettyxml(indent="    ")
        
        clean_xml = "\n".join([line for line in pretty_xml_str.split("\n") if line.strip()])
        
        print("\n--- GENERATED XML FRAGMENT ---")
        print(clean_xml)
        
    except FileNotFoundError as e:
        print(f"Initialization Error: Could not find required mapping file: {e}")
