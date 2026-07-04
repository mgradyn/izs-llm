from pydantic import BaseModel, Field, field_validator
import re

class Node(BaseModel):
    subgraph: str | None = Field(default=None)

    @field_validator('subgraph', mode='before')
    @classmethod
    def validate_subgraph(cls, v):
        if isinstance(v, str) and v:
            v = v.strip()
            v = v.replace(' ', '_')
            v = re.sub(r'[^a-zA-Z0-9_]', '', v)
            if v and not re.match(r'^[a-zA-Z_]', v):
                v = 'sg_' + v
            if not v:
                return None
        return v

n1 = Node(subgraph='Quality Control')
print(n1.subgraph)

n2 = Node(subgraph='123 Test!')
print(n2.subgraph)
