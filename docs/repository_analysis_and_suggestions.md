# Repository Analysis and Suggestions

This document provides a high-level analysis of the ContractDMS repository and offers suggestions for improvement, with a special focus on the AI-assisted letter drafting workflow.

## 1. High-Level Repository Structure

The repository is a monorepo containing two main projects:

*   **`client/`**: A modern frontend application built with **React**, **Vite**, **TypeScript**, and **Tailwind CSS**. It uses `shadcn/ui` for components, `tanstack/react-query` for server state management, and `vitest` for testing. The code is well-structured, with clear separation of pages, components, and services.
*   **`backend/`**: A **Python** backend built with the **FastAPI** framework. It appears to use MongoDB as a database (inferred from `bson.ObjectId` usage). The structure is logical, with directories for routers, models, services, and core logic.

## 2. TODO & General Suggestions

Based on the repository study, here is a list of general suggestions:

*   **Backend Dependencies**: The `backend/requirements.txt` and `backend/rbac_backend/requirements.txt` files are either empty or unreadable. This is a critical issue. The project dependencies should be explicitly listed to ensure reproducible builds.
    *   **Action**: Regenerate the `requirements.txt` file using `pip freeze > requirements.txt`.
*   **Environment Variables**: The repository contains `.env` files. These files should not be committed to version control. Instead, a `.env.example` file should be provided as a template.
    *   **Action**: Add `.env` to the `.gitignore` file and ensure `.env.example` files are up-to-date.
*   **Testing**: While a `tests` directory exists in the backend, the extent and coverage of the tests are unknown. Given the complexity of the application, robust testing is crucial.
    *   **Action**: Expand test coverage for both backend and frontend, focusing on business logic, API endpoints, and UI interactions.
*   **Hardcoded Models**: The AI model names (`gpt-4`, `text-embedding-3-small`) are hardcoded in `backend/rbac_backend/routers/deep_planning.py`.
    *   **Action**: Move these model names to the `settings` configuration to allow for easier updates and environment-specific configurations (e.g., using a cheaper model in development).
*   **Error Handling**: The frontend has some fallback mechanisms (e.g., in `DeepPlanningAssistant.tsx`), but the backend could benefit from more specific error handling. For example, if the OpenAI API fails, the backend could return a more specific error code or message.

## 3. AI Letter Workflow: Analysis and Improvements

The AI-assisted letter drafting feature (`DeepPlanningAssistant`) is a powerful and well-implemented feature. It uses a Retrieval-Augmented Generation (RAG) pattern by finding similar letters to enrich the prompt, which is excellent.

### How it Works

1.  **Frontend**: The `DeepPlanningAssistant.tsx` component collects `document_ids`, subject, recipient, and other context from the user.
2.  **API Call**: It sends this information to the `/api/deep-planning/generate-draft` endpoint.
3.  **Backend**: The `deep_planning.py` router handles the request:
    a. It extracts content from the provided `document_ids`.
    b. It finds similar letters from the database using text embeddings and cosine similarity.
    c. It uses `gpt-4` to extract key points and clauses from the document context.
    d. It constructs a detailed final prompt for `gpt-4`, including the extracted context and similar letters, to generate the draft.
4.  **Response**: The generated draft, key points, and other metadata are returned to the frontend to be displayed to the user.

### Suggestions for Improvement

*   **Streaming Responses**: The AI generation can take time. Instead of making the user wait for the full response, stream the generated letter content back to the frontend as it's being produced by the AI. This would significantly improve the user experience.
    *   **Implementation**: Use `StreamingResponse` in FastAPI and `fetch` with a readable stream on the frontend.
*   **Interactive Clause/Point Selection**: The AI automatically extracts key points and clauses. An improvement would be to present these extracted items to the user as a checklist. The user could then select which points or clauses to include in the final draft, giving them more control.
*   **Refine "Similar Letters" Feature**:
    *   **UI**: Display the content of the similar letters in the UI, perhaps in a modal or a side panel, so the user can review them for context.
    *   **Similarity Threshold**: The `0.3` cosine similarity threshold is hardcoded. This could be made configurable or even dynamically adjusted based on the number of results found.
*   **Prompt Engineering & Management**: The prompts are hardcoded within the Python functions. As the number of AI features grows, these prompts can become difficult to manage.
    *   **Action**: Externalize prompts into a separate template file or a prompt management system. This allows for easier editing and versioning of prompts without changing the code.
*   **Cost and Performance Monitoring**: The use of `gpt-4` can be expensive.
    *   **Action**: Log the token usage for each API call to monitor costs. Consider using a faster, cheaper model like `gpt-3.5-turbo` for simpler tasks or as a user-selectable option.
*   **User Feedback Loop**: Add a mechanism for users to rate the quality of the generated draft (e.g., a thumbs up/down button). This feedback can be used to fine-tune the prompts or the RAG strategy over time.
