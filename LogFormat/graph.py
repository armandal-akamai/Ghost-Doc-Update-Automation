from typing import List, Optional, Literal
from pydantic import BaseModel, Field

# Deepest Base Components (Leaves & Elements)

# --- ENUMS ---
class EnumItem(BaseModel):
    id: str = Field(description="The ID or value of the enum option.")
    doc: Optional[str] = Field(None, description="The documentation explaining this enum.")

# --- SUB-CHARS ---
class SubCharItem(BaseModel):
    id: str = Field(description="The character ID of the sub-char.")
    doc: Optional[str] = Field(None, description="The documentation explaining this sub-char.")

class SubChars(BaseModel):
    sub_char_items: Optional[List[SubCharItem]] = Field(default_factory=list)

# --- ADVANCED STATUS CHARS ---
class StatusCharItem(BaseModel):
    id: str = Field(description="The unique string ID of the status character.")
    duplicate: Optional[str] = Field(None, description="Tracks structural duplication parameters.")
    since: Optional[str] = Field(None, description="The release version this character was introduced.")
    until: Optional[str] = Field(None, description="The release version this character was retired.")
    streaming_related: Optional[str] = Field(None, description="Custom token string tracking streaming properties.")
    doc: Optional[str] = Field(None, description="The text documentation explanation for this status char.")

class StatusCharGroup(BaseModel):
    since: Optional[str] = Field(None, description="The release version this structural group became active.")
    until: Optional[str] = Field(None, description="The release version this structural group was deprecated.")
    doc: Optional[str] = Field(None, description="Contextual summary detailing this versioned group block.")
    status_char_items: Optional[List[StatusCharItem]] = Field(default_factory=list)

class StatusChars(BaseModel):
    repeat_alphabetized: Optional[str] = Field(None, description="Toggle configuration tracking sorting logic behavior.")
    status_char_groups: Optional[List[StatusCharGroup]] = Field(default_factory=list)
    status_char_items: Optional[List[StatusCharItem]] = Field(default_factory=list)

# --- BITMASK ---
class BitvalItem(BaseModel):
    id: str = Field(description="The numeric ID for this specific bit value.")
    doc: Optional[str] = Field(None, description="The documentation explaining this bit value.")

class BitflagItem(BaseModel):
    id: str = Field(description="The numeric ID of the bitflag.")
    doc: Optional[str] = Field(None, description="The documentation for this specific bitflag.")

class BitfieldItem(BaseModel):
    id: str = Field(description="The numeric range ID of the bitfield (e.g., '0-3').")
    doc: Optional[str] = Field(None, description="The documentation for this bitfield range.")
    bitval_items: Optional[List[BitvalItem]] = Field(default_factory=list, description="The specific bit values inside this field.")

class Bitmask(BaseModel):
    bitflag_items: Optional[List[BitflagItem]] = Field(default_factory=list)
    bitfield_items: Optional[List[BitfieldItem]] = Field(default_factory=list)


# Intermediate Containers & Splitters

# --- SPLITTERS ---
class Splitter(BaseModel):
    pattern: str = Field(description="The delimiter character used to split the string, e.g., '|', ';', or ':'.")
    regex: Optional[str] = Field(None, description="Set to 'yes' if the pattern is a regular expression.")
    max_splits: Optional[str] = Field(None, description="The maximum number of times to split the string.")
    size: Optional[int] = Field(None, description="Size parameter for the splitter layout configuration.")

class NameValueSplitter(BaseModel):
    pattern: str = Field(description="The delimiter separating a name from a value, usually '='.")
    regex: Optional[str] = Field(None, description="Set to 'yes' if the pattern is a regular expression.")

# --- SUB-FIELDS (Recursive Block Container) ---
class SubFieldItem(BaseModel):
    id: str = Field(description="The numeric ID of this sub-field, e.g., '1', '2'.")
    since: Optional[str] = Field(None, description="The Ghost release version this sub-field was introduced.")
    doc: Optional[str] = Field(None, description="The documentation explaining what this sub-field tracks.")
    
    enums: Optional[List[EnumItem]] = Field(default=None)
    bitmask: Optional[Bitmask] = None
    sub_chars: Optional[SubChars] = None
    parse_info: Optional[ParseInfo] = None
    status_chars: Optional[StatusChars] = None
    
    # Nested Recursion Target
    sub_fields: Optional['SubFields'] = None 
    named_sub_fields: Optional['NamedSubFields'] = None

class SubFields(BaseModel):
    splitter: Optional[Splitter] = None
    parse_info: Optional[ParseInfo] = None
    sub_field_items: Optional[List[SubFieldItem]] = Field(default_factory=list)

# --- NAMED SUB-FIELDS ---
class NamedFieldItem(BaseModel):
    id: str = Field(description="The string ID of the named field, e.g., 'wco', 'dev', 'chk'.")
    since: Optional[str] = Field(None, description="The Ghost release version this named field was introduced.")
    until: Optional[str] = Field(None, description="The Ghost release version this named field was retired.")
    reporting_level: Optional[str] = Field(None, description="The data reporting level, e.g., 'billing', 'portal', 'debug'.")
    doc: Optional[str] = Field(None, description="The documentation explaining this named field.")

    parse_info: Optional[ParseInfo] = None
    enums: Optional[List[EnumItem]] = Field(default=None)
    bitmask: Optional[Bitmask] = None
    sub_fields: Optional[SubFields] = None
    sub_chars: Optional[SubChars] = None
    status_chars: Optional[StatusChars] = None

class NamedSubFields(BaseModel):
    splitter: Optional[Splitter] = None
    name_value_splitter: Optional[NameValueSplitter] = None
    parse_info: Optional[ParseInfo] = None
    named_fields: Optional[List[NamedFieldItem]] = Field(default_factory=list)


# Top-Level Fields and Field Groups Routing

class ParseInfo(BaseModel):
    type: Optional[str] = Field(None, description="The data type parser token rule, e.g., 'epochtime' or 'string_error'.")
    skipsub: Optional[int] = Field(None, description="Flag indicating if sub-field skipping is active.")
    repeat: Optional[int] = Field(None, description="Flag indicating repeating elements.")
    erase: Optional[str] = Field(None, description="Characters to erase from the parsed string layout.")
    chars_as_enum: Optional[int] = Field(None, description="Flag to treat characters in field directly as an enum registry lookup.")
    joiner: Optional[str] = Field(None, description="The delimiter token used to join parsed components.")

class LogField(BaseModel):
    id: str = Field(description="The numeric string ID matching the log-field element, e.g., '14' or '61'.")
    since: Optional[str] = Field(None, description="The Ghost release version this field was introduced.")
    until: Optional[str] = Field(None, description="The Ghost release version this field was retired/deprecated.")
    status_id: Optional[str] = Field(None, description="Custom status identifier attribute pointing to validation sets, e.g., 'r_ims'.")
    contains_streaming_flag: Optional[str] = Field(None, description="Custom marker text tag used for internal media streaming telemetry flags.")
    include_fields: Optional[int] = Field(None, description="Positional display filter hierarchy rule value.")
    non_configurable: Optional[bool] = Field(None, description="Boolean toggle mapping if this layout layer is locked against edits.")
    doc: Optional[str] = Field(None, description="The plain text paragraph explaining what this log field tracks.")
    
    parse_info: Optional[ParseInfo] = None
    enums: Optional[List[EnumItem]] = Field(default=None)
    bitmask: Optional[Bitmask] = None
    sub_chars: Optional[SubChars] = None
    status_chars: Optional[StatusChars] = None 
    sub_fields: Optional[SubFields] = None
    named_sub_fields: Optional[NamedSubFields] = None

class LogFieldGroup(BaseModel):
    since: Optional[str] = Field(None, description="The Ghost release version this group block updates became active.")
    until: Optional[str] = Field(None, description="The Ghost release version this group block updates were deprecated.")
    doc: Optional[str] = Field(None, description="The block introductory summary sentence describing the group revision context.")
    log_fields: Optional[List[LogField]] = Field(default_factory=list, description="List of sequential log fields tracked inside this version boundary group.")


# Log Line Containers & Master Payload Router

class LogLineVersionUpdate(BaseModel):
    id: str = Field(description="The version ID string of the log line schema, e.g., '1', '2', '10'.")
    since: Optional[str] = Field(None, description="The Ghost release version this line structure version became active.")
    until: Optional[str] = Field(None, description="The Ghost release version this line structure version was active until.")
    access_log: Optional[str] = Field(None, description="Toggle indicator ('yes'/'no') for access log generation tracking.")
    ddc_log: Optional[str] = Field(None, description="Toggle indicator ('yes'/'no') for DDC log generation tracking.")
    activity_log: Optional[str] = Field(None, description="Toggle indicator ('yes'/'no') for activity log generation tracking.")
    doc: Optional[str] = Field(None, description="The textual documentation or paragraph describing this specific historical version context.")
    
    standalone_fields: Optional[List[LogField]] = Field(default_factory=list)
    log_field_groups: Optional[List[LogFieldGroup]] = Field(default_factory=list)

class LogFieldsPayload(BaseModel):
    standalone_fields: Optional[List[LogField]] = Field(default_factory=list)
    log_field_groups: Optional[List[LogFieldGroup]] = Field(default_factory=list)

class LogLineUpdate(BaseModel):
    id: Optional[str] = Field(description="The unique single character ID token targeting the master log line layout block, e.g., 'r', 'f', 'W', 'v'.")
    name: Optional[str] = Field(None, description="The full descriptive name of the log line category, e.g., 'Client Requests'.")
    access_log: Optional[str] = Field(None, description="Log context configuration selector mapping.")
    ddc_log: Optional[str] = Field(None, description="Log context configuration selector mapping.")
    activity_log: Optional[str] = Field(None, description="Log context configuration selector mapping.")
    check_continuity: Optional[str] = Field(None, description="Control flag tracking structural continuity.")
    since: Optional[str] = Field(None, description="The release version constraint boundary.")
    until: Optional[str] = Field(None, description="The release version constraint boundary.")
    doc: Optional[str] = Field(None, description="Introductory reference explanation summary for the line layout.")
    
    log_line_versions: Optional[List[LogLineVersionUpdate]] = Field(default_factory=list)
    log_fields: Optional[LogFieldsPayload] = Field(default=None)

class ChangelogEntry(BaseModel):
    ghost_version: Optional[str] = Field(description="The release version string associated with this history entry, e.g., '22.5.1'.")
    date: Optional[str] = Field(description="The exact calendar date of the commit formatted strictly as YYYY-MM-DD.")
    author: Optional[str] = Field(description="The first and last name of the developer publishing the edit pass.")
    change_summary: Optional[str] = Field(description="A clean sentence outlining the scope of fields or components added/modified.")

# --- THE MASTER ROUTER PAYLOAD ---
class CompilerPayload(BaseModel):
    action_type: Literal["append_changelog", "update_logline"] = Field(
        description="The target route trigger selector."
    )
    changelog_payload: Optional[ChangelogEntry] = Field(
        None, description="Populated ONLY if action_type evaluates exactly to 'append_changelog'.",
        alias="changelog_entry"
    )
    logline_payload: Optional[LogLineUpdate] = Field(
        None, description="Populated ONLY if action_type evaluates exactly to 'update_logline'.",
        alias="log-line"
    )

# Safely resolve nested cross-references in Pydantic v2 execution environments
SubFieldItem.model_rebuild()
NamedFieldItem.model_rebuild()
