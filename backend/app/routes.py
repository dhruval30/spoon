# File: backend/app/routes.py
# Purpose: Defines all API endpoints and page-serving routes.

from flask import request, jsonify, render_template, send_file, send_from_directory
import os
from io import BytesIO
from werkzeug.utils import secure_filename
import concurrent.futures

# LangChain imports
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from app import app
from . import services

# In-memory store for a single session (works on Render free tier)
session_data = {
    "docs": None,
    "file_manifest": None,
    "repo_url": None,
    "pdf_docs": None,
    "document_filename": None, # Stores filename for the viewer
    "document_content": None,  # Stores raw file bytes for the viewer
    "document_mimetype": None # Stores the file's mimetype
}

# --- Page Rendering Routes ---

@app.route('/')
def index():
    """Renders the landing page."""
    return render_template('index.html')

@app.route('/analysis')
def analysis():
    """Renders the main analysis chat page."""
    return render_template('analysis.html')


# --- LLM and API Logic ---

@app.route('/api/load_repo', methods=['POST'])
def load_repo_route():
    """Loads a repository's data and ensures all other data is cleared."""
    global session_data
    data = request.get_json()
    repo_url = data.get('url')

    if not repo_url:
        return jsonify({"error": "Repository URL is required"}), 400

    try:
        # Clear any leftover document data from previous sessions
        session_data["pdf_docs"] = None
        session_data["document_filename"] = None
        session_data["document_content"] = None
        session_data["document_mimetype"] = None

        docs = services.fetch_repo_docs(repo_url)
        session_data["docs"] = docs
        
        file_manifest = "\n".join([doc.metadata.get("source", "") for doc in docs])
        session_data["file_manifest"] = file_manifest
        
        session_data["repo_url"] = repo_url
        
        return jsonify({"message": f"Successfully loaded and processed {len(docs)} document chunks from: {repo_url}. Ready for questions."}), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        print(f"Unexpected error in /api/load_repo: {e}")
        return jsonify({"error": "An unexpected server error occurred."}), 500


@app.route('/api/load_file', methods=['POST'])
def load_file_route():
    """Loads a non-PDF file (.md, .txt) entirely in-memory."""
    global session_data
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    try:
        # Clear all other session data for a clean slate
        session_data.update({
            "pdf_docs": None, "repo_url": None, "docs": None, "file_manifest": None
        })

        # Read the entire file into memory for both processing and viewing
        file_content_bytes = file.read()
        
        # Store for the viewer
        session_data["document_filename"] = secure_filename(file.filename)
        session_data["document_content"] = file_content_bytes
        session_data["document_mimetype"] = file.mimetype
        
        # Create a new in-memory stream for the processing function
        file_stream_for_processing = BytesIO(file_content_bytes)
        file_stream_for_processing.filename = file.filename # Add filename attribute for the service
        
        docs = services.process_uploaded_file_docs(file_stream_for_processing)
        session_data["docs"] = docs
        
        file_manifest = "\n".join([doc.metadata.get("source", "") for doc in docs])
        session_data["file_manifest"] = file_manifest
        
        return jsonify({"message": f"Successfully loaded {len(docs)} document chunks from: {file.filename}. Ready for questions."}), 200
        
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        print(f"Unexpected error in /api/load_file: {e}")
        return jsonify({"error": "An unexpected server error occurred."}), 500


@app.route('/api/load_pdf', methods=['POST'])
def load_pdf_route():
    """Loads a PDF file entirely in-memory."""
    global session_data
    if 'file' not in request.files:
        return jsonify({"error": "No file was included in the request."}), 400
    
    pdf_file = request.files['file']
    if pdf_file.filename == '':
        return jsonify({"error": "No file was selected."}), 400

    try:
        # Clear all other session data for a clean slate
        session_data.update({
            "docs": None, "repo_url": None, "pdf_docs": None, "file_manifest": None
        })

        # Read the entire file into memory for both processing and viewing
        file_content_bytes = pdf_file.read()

        # Store for the viewer
        session_data["document_filename"] = secure_filename(pdf_file.filename)
        session_data["document_content"] = file_content_bytes
        session_data["document_mimetype"] = pdf_file.mimetype

        # Create a new in-memory stream for the processing function
        file_stream_for_processing = BytesIO(file_content_bytes)
        file_stream_for_processing.filename = pdf_file.filename # Add filename attribute for the service

        pdf_docs = services.process_pdf_file_and_chunk(file_stream_for_processing)
        session_data["pdf_docs"] = pdf_docs
        
        return jsonify({
            "message": f"Successfully processed '{pdf_file.filename}' into {len(pdf_docs)} chunks. You may now ask questions about the PDF."
        }), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        print(f"Error in new /api/load_pdf route: {e}")
        return jsonify({"error": "A server error occurred while processing the PDF."}), 500


@app.route('/api/ask_question', methods=['POST'])
# In backend/app/routes.py

@app.route('/api/ask_question', methods=['POST'])
@app.route('/api/ask_question', methods=['POST'])
def ask_question_route():
    """
    Handles a user's question about a repository or non-PDF document.
    """
    global session_data
    data = request.get_json()
    question = data.get('question')

    all_docs = session_data.get("docs")
    file_manifest = session_data.get("file_manifest")

    if not question:
        return jsonify({"error": "A question is required"}), 400
    if not all_docs:
        return jsonify({"error": "Please load a repository or file first."}), 400
        
    try:
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash-latest", google_api_key=gemini_api_key, temperature=0.0)

        # Step 1: Classify if the input is a real question or just conversation
        intent_classifier_prompt = ChatPromptTemplate.from_template(
            """
            You are an intent classifier. Your job is to determine if a user's input is a technical question about a code repository or a simple conversational reply/question.
            Possible intents are: "technical_question" or "conversational_reply".

            - "technical_question": The user is asking for information ABOUT THE CODE REPOSITORY, its structure, functionality, etc. This also includes requests for more detail or elaboration on a previous answer (e.g., "what does this file do?", "explain the tech stack", "tell me more about that", "go into detail please").
            - "conversational_reply": The user is NOT asking about the code. This includes simple social responses (e.g., "great", "thanks"), greetings, and direct questions to you, the AI (e.g., "what is your name?", "who are you?", "how was your day?").

            Based on the following user input, what is the intent? Respond with ONLY "technical_question" or "conversational_reply".

            User Input: "{question}"
            Intent:
            """
        )
        intent_classifier_chain = intent_classifier_prompt | llm | StrOutputParser()
        intent = intent_classifier_chain.invoke({"question": question})

        print(f"--- User Intent Classified as: {intent} ---")

        if "conversational_reply" in intent.lower():
            conversation_responder_prompt = ChatPromptTemplate.from_template(
                """
                You are Spoon, a friendly and helpful AI assistant for code analysis. The user has just said something to you directly. Provide a brief, polite, and natural response.

                User's input: "{question}"
                Your response:
                """
            )
            conversation_chain = conversation_responder_prompt | llm | StrOutputParser()
            answer = conversation_chain.invoke({"question": question})
            return jsonify({"response": answer})
        
        else:
            # Step 2: Classify if the technical question is broad or specific.
            query_classifier_prompt = ChatPromptTemplate.from_template(
                """
                You are a query classifier. Your task is to determine if a user's question about a codebase is a "broad_query" or a "specific_query".

                - "broad_query": The user is asking for a high-level, general overview. These questions require understanding the whole project.
                  Examples: "what does this codebase do?", "explain this project", "give me a summary", "what is the overall architecture?", "what are the key features?", "generate potential use cases"

                - "specific_query": The user is asking about a particular file, function, or focused concept. These questions can be answered by looking at a small number of files.
                  Examples: "what does the User model in `user.py` do?", "explain the `calculate_payment` function", "where are the database credentials stored?"

                Based on the user's question, what is the query type? Respond with ONLY "broad_query" or "specific_query".

                User Question: "{question}"
                Query Type:
                """
            )
            query_classifier_chain = query_classifier_prompt | llm | StrOutputParser()
            query_type = query_classifier_chain.invoke({"question": question})
            print(f"--- Query classified as: {query_type} ---")

            relevant_docs = []

            # Step 3: Decide whether to use the file-picking planner based on the query type.
            if "broad_query" in query_type.lower():
                print("--- Broad query detected. Using full context. ---")
                relevant_docs = all_docs
            else: # "specific_query"
                print("--- Specific query detected. Running planner to find relevant files... ---")
                planner_prompt = ChatPromptTemplate.from_template(
                    """
                    You are an expert software engineer acting as a query planner.
                    Your task is to identify the most relevant files to answer the user's question based on the provided file manifest.
                    User Question: "{question}"
                    Available Files:
                    {file_manifest}
                    Instructions:
                    - List the full paths of the most relevant files, separated by commas.
                    - Do not explain your reasoning.
                    - If no files seem relevant, respond with "README.md".
                    - Be concise. Your output should only be a comma-separated list of file paths.
                    Relevant Files:
                    """
                )
                planner_chain = planner_prompt | llm | StrOutputParser()
                
                relevant_files_str = planner_chain.invoke({ "question": question, "file_manifest": file_manifest })
                relevant_file_paths = [f.strip() for f in relevant_files_str.split(',') if f.strip()]
                print(f"--- Planner identified relevant files: {relevant_file_paths} ---")

                relevant_docs = [doc for doc in all_docs if doc.metadata.get("source") in relevant_file_paths]
                
                if not relevant_docs: # Fallback
                    relevant_docs = [doc for doc in all_docs if "README.md" in doc.metadata.get("source", "")]
            
            # Step 4: The final answering step, now with the correct context.
            context_for_responder = "\n\n---\n\n".join(
                [f"File: {doc.metadata.get('source')}\n\nContent:\n{doc.page_content}" for doc in relevant_docs]
            )

            responder_prompt = ChatPromptTemplate.from_template(
                """
                You are Spoon, an expert AI software engineer. Your primary function is to analyze a given codebase and answer questions as a senior developer would.

                **Core Instructions:**
                1.  **Analyze and Infer**: Your answer MUST be based on the provided CONTEXT. Do not just search for literal text. You are expected to read, understand, and interpret the code and text files to form your conclusions.
                2.  **Synthesize and Generate**: When asked for abstract concepts like the project's 'purpose', 'problem it solves', 'intended users', or to **generate potential 'use cases'**, you MUST synthesize these answers by analyzing the entire context. Look at API routes, UI elements, dependencies, and comments to determine the application's function, audience, and practical applications.
                3.  **Be Creative with Use Cases**: When asked for use cases, think practically about who would benefit from this tool and what specific problems it would solve for them. Generate a detailed list based on the project's confirmed features.
                4.  **No External Knowledge**: Do not use any information outside of the provided CONTEXT. If the context genuinely doesn't provide enough information to form a conclusion, only then should you state that.

                **CONTEXT:**
                {context}

                **Question:** {question}

                **Answer:**
                """
            )

            responder_chain = responder_prompt | llm | StrOutputParser()
            
            print(f"--- Running Responder Chain with {len(relevant_docs)} documents... ---")
            answer = responder_chain.invoke({
                "context": context_for_responder,
                "question": question
            })
            
            return jsonify({"response": answer}), 200
        
    except Exception as e:
        print(f"Unexpected error in /api/ask_question: {e}")
        return jsonify({"error": f"Failed to get a response from the AI. Error: {str(e)}"}), 500


@app.route('/api/ask_pdf_question', methods=['POST'])
def ask_pdf_question_route():
    """
    Handles questions exclusively about a processed PDF.
    """
    global session_data
    data = request.get_json()
    question = data.get('question')
    pdf_docs = session_data.get("pdf_docs")

    if not question:
        return jsonify({"error": "A question is required."}), 400
    if not pdf_docs:
        return jsonify({"error": "A PDF document must be loaded before asking questions."}), 400

    try:
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash-latest", google_api_key=gemini_api_key, temperature=0.0)

        pdf_intent_prompt = ChatPromptTemplate.from_template(
            """
            Classify the user's intent for a query about a PDF document. The intents are "pdf_query" or "general_chat".

            - "pdf_query": The user is asking something directly related to the content of the PDF document (e.g., "summarize this document", "what does section 3 say about regulations?", "who is the author?").
            - "general_chat": The user is making a conversational comment, a greeting, or asking a question not related to the PDF content (e.g., "that's interesting", "thank you", "what else can you do?").

            Based on the user's query below, respond with ONLY "pdf_query" or "general_chat".

            User Query: "{question}"
            Intent:
            """
        )
        pdf_intent_chain = pdf_intent_prompt | llm | StrOutputParser()
        intent = pdf_intent_chain.invoke({"question": question})
        print(f"--- PDF Intent Classified as: {intent} ---")

        if "general_chat" in intent.lower():
            pdf_convo_prompt = ChatPromptTemplate.from_template("You are an AI assistant helping a user understand a PDF. The user has made a conversational comment. Respond politely and briefly. User's comment: '{question}'")
            pdf_convo_chain = pdf_convo_prompt | llm | StrOutputParser()
            answer = pdf_convo_chain.invoke({"question": question})
            return jsonify({"response": answer})

        else:
            chunk_manifest = "\n".join([
                f"Chunk ID: {doc.metadata['chunk_id']}, Start of content: {doc.page_content[:150]}..."
                for doc in pdf_docs
            ])

            pdf_planner_prompt = ChatPromptTemplate.from_template(
                """
                You are a research assistant. Your goal is to find the most relevant sections (chunks) of a PDF document to answer a user's question.
                Review the user's question and the provided manifest of document chunks.

                User Question: "{question}"

                Document Chunk Manifest:
                {chunk_manifest}

                Instructions:
                - Identify the Chunk IDs that are most likely to contain the answer.
                - List only the relevant Chunk IDs, separated by commas.
                - If the question is a general summary, list all chunk IDs.
                - Example Response: 1,5,12

                Relevant Chunk IDs:
                """
            )
            pdf_planner_chain = pdf_planner_prompt | llm | StrOutputParser()
            
            print("--- Running PDF Planner to find relevant chunks... ---")
            relevant_chunks_str = pdf_planner_chain.invoke({
                "question": question,
                "chunk_manifest": chunk_manifest
            })
            
            try:
                relevant_chunk_ids = [int(id.strip()) for id in relevant_chunks_str.split(',') if id.strip().isdigit()]
            except (ValueError, TypeError):
                relevant_chunk_ids = [doc.metadata['chunk_id'] for doc in pdf_docs]

            print(f"--- PDF Planner identified relevant chunk IDs: {relevant_chunk_ids} ---")

            relevant_pdf_docs = [doc for doc in pdf_docs if doc.metadata.get("chunk_id") in relevant_chunk_ids]

            context_for_pdf_responder = "\n\n---\n\n".join(
                [f"Content from Chunk {doc.metadata.get('chunk_id')}:\n{doc.page_content}" for doc in relevant_pdf_docs]
            )

            pdf_responder_prompt = ChatPromptTemplate.from_template(
                """
                You are Spoon, an expert AI research assistant. Your task is to answer the user's question based on the provided text from a PDF document.

                **Core Instructions:**
                1.  **Synthesize, Don't Hallucinate**: You MUST base your entire answer on the "DOCUMENT CONTEXT". You are encouraged to synthesize information from multiple parts of the context to answer broader questions (like "what is this story about?"). However, do not add information or make assumptions that are not supported by the text.
                2.  **Handle Missing Information**: If you are truly unable to answer the question from the provided context, state that the answer is not available in the provided text. Do not guess.
                3.  **Format for Clarity**: Use Markdown for clear formatting (e.g., lists, bolding) to present the answer.

                DOCUMENT CONTEXT:
                {context}

                User's Question: {question}

                Answer:
                """
            )
            pdf_responder_chain = pdf_responder_prompt | llm | StrOutputParser()
            
            print(f"--- Running PDF Responder with {len(relevant_pdf_docs)} documents... ---")
            answer = pdf_responder_chain.invoke({
                "context": context_for_pdf_responder,
                "question": question
            })
            
            return jsonify({"response": answer}), 200

    except Exception as e:
        print(f"Error in new /api/ask_pdf_question route: {e}")
        return jsonify({"error": f"An AI error occurred while answering the question about the PDF. Error: {str(e)}"}), 500


@app.route('/api/get_repo_tree', methods=['GET'])
def get_repo_tree_route():
    global session_data
    if not session_data.get("repo_url"):
        return jsonify({"error": "No repository loaded."}), 400
    try:
        tree_data = services.fetch_repo_tree(session_data["repo_url"])
        return jsonify({"tree": tree_data}), 200
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"error": "Failed to fetch repository tree."}), 500


@app.route('/api/get_file_content', methods=['POST'])
def get_file_content_route():
    global session_data
    data = request.get_json()
    file_path = data.get('file_path')
    if not file_path:
        return jsonify({"error": "File path is required"}), 400
    if not session_data.get("repo_url"):
        return jsonify({"error": "No repository loaded."}), 400
    try:
        file_data = services.fetch_file_content(session_data["repo_url"], file_path)
        return jsonify({"file": file_data}), 200
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"error": "Failed to fetch file content."}), 500
    

@app.route('/api/get_document_content', methods=['GET'])
def get_document_content_route():
    """Serves the last uploaded document for viewing."""
    global session_data
    filename = session_data.get("document_filename")

    if not filename:
        return jsonify({"error": "No document has been uploaded in this session."}), 404

    try:
        print(f"Serving file for viewing: {filename} from {app.config['UPLOAD_FOLDER']}")
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except FileNotFoundError:
        return jsonify({"error": "Could not find the document file to display."}), 404