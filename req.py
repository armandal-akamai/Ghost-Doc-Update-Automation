from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

class State(BaseModel):
    name : str = Field(description="name of the person")
    age : int = Field(description="Age of the person")

llm = ChatOpenAI(
    model="qwen3:14b",
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)
structLlm = llm.with_structured_output(State)

print(structLlm.invoke("The name of the person is Aritra with age 30."))


