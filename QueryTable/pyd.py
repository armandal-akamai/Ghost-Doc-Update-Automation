from pydantic import BaseModel, Field
from typing import List, Optional

class ColumnData(BaseModel):
    name: Optional[str] = Field(
        default=None, 
        description="The exact name of the database column. Must not contain spaces."
    )
    type: Optional[str] = Field(
        default=None, 
        description="The data type of the column (e.g., 'int', 'string'). If the Jira ticket does not specify a type, default to 'string'."
    )
    description: Optional[str] = Field(
        default=None, 
        description="The description of the column. CRITICAL: This text MUST be wrapped in explicitly paired HTML tags (e.g., <p>Your description here</p> or <b>Your description here</b>). Do not use plain text."
    )

class QueryData(BaseModel):
    query_to_send: Optional[str] = Field(
        default=None, 
        description="The exact SQL query string to be executed (e.g., 'select * from mytable limit 1')."
    )
    query_desc: Optional[str] = Field(
        default=None, 
        description="A brief, plain-text description of what the query accomplishes."
    )
    query_result: Optional[str] = Field(
        default=None, 
        description="A sample of the expected output or result set. Format as raw text representing a data table."
    )

class MDRQueryTable(BaseModel):
    name: Optional[str] = Field(
        default=None, 
        description="The unique base name of the table. This must exactly match the intended filename (without the .xml extension)."
    )
    publisher: Optional[str] = Field(
        default=None, 
        description="The team or individual publishing this table."
    )
    owner: Optional[str] = Field(
        default=None, 
        description="The email address of the table owner. Extract this from the Jira ticket assignee. If none is found, use a default fallback."
    )
    table_desc: Optional[str] = Field(
        default=None, 
        description="The overall description of the table's purpose. CRITICAL: This text MUST be wrapped in explicitly paired HTML tags (e.g., <p>Table description here</p>)."
    )
    columns: Optional[List[ColumnData]] = Field(
        default_factory=list, 
        description="A list of all documented columns in the table. You MUST extract every column mentioned in the ticket."
    )
    useful_queries: Optional[List[QueryData]] = Field(
        default_factory=list, 
        description="A list of useful example queries. If the ticket doesn't provide one, generate a basic 'select *' query."
    )
    see_also: Optional[List[str]] = Field(
        default_factory=list, 
        description="Optional list of related table names or documentation links mentioned in the ticket."
    )
    providers: Optional[List[str]] = Field(
        default_factory=list, 
        description="Optional list of networks/providers to associate with this table (e.g., 'ESSL', 'FreeFlow', 'NetStorage')."
    )
