# TODO for Fixing LangGraph Draft 404 Error

## Completed Steps

- [x] Edit backend/rbac_backend/main.py: Added import for ai_assistant router and included it with prefix="/api" and tags=["ai-assistant"].
- [x] Fix SyntaxError in backend/rbac_backend/routers/ai_assistant.py: Added quotes around dict keys in \_sanitize_draft_request payload to resolve invalid syntax.

## Pending Steps

- [ ] Restart or reload the backend server (e.g., run `uvicorn rbac_backend.main:app --reload` or restart docker-compose) to apply the changes.
- [ ] Test the endpoint: Verify POST /api/ai-assistant/langgraph/draft works (e.g., via curl or FastAPI /docs).
- [ ] Test in UI: Navigate to LetterStrategicPlanPage and click "Generate Strategy Plan" button; confirm no 404 error and successful generation.
- [ ] If new errors (e.g., 500 from AIService), read and fix backend/rbac_backend/services/ai_service.py.
- [ ] Update this TODO.md after each step.
