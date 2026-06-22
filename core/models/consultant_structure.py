from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ConsultantOutput(BaseModel):
    response_to_user: str = Field(
        description="Your conversational reply to the user. Ask questions or confirm steps."
    )
    status: Literal["CHATTING", "APPROVED"] = Field(
        description="Set to CHATTING if the user is still making changes. Set to APPROVED only when the user says they are ready to build."
    )
    draft_plan: str | None = Field(
        default=None,
        description="If APPROVED, write a detailed step-by-step summary of the pipeline for the Architect."
    )
    strategy_selector: Literal["EXACT_MATCH", "ADAPTED_MATCH", "CUSTOM_BUILD"] | None = Field(
        default=None,
        description="If APPROVED, select how to build this based on the available RAG templates."
    )
    used_template_id: str | None = Field(
        default=None,
        description="CRITICAL: MUST be the EXACT template ID from the RAG context (e.g., 'my_special_pipeline'). DO NOT invent or guess names."
    )
    selected_component_ids: list[str] = Field(
        default_factory=list,
        description="CRITICAL: MUST be a list of EXACT component IDs from the RAG context (e.g., ['process_data_mapper']). Do not use shorthand like 'mapper'."
    )

    @field_validator('selected_component_ids', mode='before')
    @classmethod
    def prevent_null_list(cls, v: Any) -> Any:
        if v is None:
            return []
        return v

    @model_validator(mode='after')
    def validate_approved_status(self) -> 'ConsultantOutput':
        if self.status == "APPROVED":
            if not self.draft_plan:
                raise ValueError("draft_plan is required when status is APPROVED.")
            if not self.strategy_selector:
                raise ValueError("strategy_selector is required when status is APPROVED.")
        return self
