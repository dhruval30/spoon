# File: backend/app/services.py
# Purpose: Contains all business logic for fetching and processing data.

import os
import re
from github import Github, GithubException
from PyPDF2 import PdfReader
from io import BytesIO

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def fetch_repo_docs(repo_url):
    """
    Fetches repository data and returns it as a list of Document objects.
    Each document represents a file or a chunk of a file.
    """
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    if not GITHUB_TOKEN:
        raise ValueError("GitHub token not set. Please set the GITHUB_TOKEN environment variable.")

    try:
        g = Github(GITHUB_TOKEN)
        repo_path = repo_url.replace('https://github.com/', '').strip('/')
        repo = g.get_repo(repo_path)
        print(f"Fetching docs for: {repo_path}")

        docs = []
        
        # 1. Add README as a document
        try:
            readme_content = repo.get_contents("README.md").decoded_content.decode('utf-8')
            docs.append(Document(page_content=readme_content, metadata={"source": "README.md"}))
        except Exception:
            # If no README, add a placeholder document
            docs.append(Document(page_content="No README.md found in the repository.", metadata={"source": "README.md"}))

        # 2. Add repository structure as a document
        structure = _get_repo_structure(repo)
        docs.append(Document(page_content=f"This is the repository file structure:\n{structure}", metadata={"source": "Repository Structure"}))

        # 3. Add source code files as documents, chunking if necessary
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=4000, chunk_overlap=200)
        
        contents = repo.get_contents("")
        while contents:
            file_content = contents.pop(0)
            if file_content.type == "dir":
                # Add sub-directory contents to the processing list
                contents.extend(repo.get_contents(file_content.path))
            else:
                # Skip binary, large, or irrelevant files
                binary_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.ico', '.zip', '.pdf', '.woff', '.woff2', '.DS_Store', 'package-lock.json']
                if any(file_content.name.lower().endswith(ext) for ext in binary_extensions) or file_content.size > 100000:
                    continue
                
                try:
                    content = file_content.decoded_content.decode('utf-8')
                    # Split large files into manageable chunks
                    chunks = text_splitter.split_text(content)
                    for i, chunk in enumerate(chunks):
                        docs.append(Document(
                            page_content=chunk, 
                            metadata={"source": file_content.path, "chunk": i}
                        ))
                except UnicodeDecodeError:
                    print(f"Skipping non-UTF-8 file: {file_content.path}")
        
        print(f"Created {len(docs)} documents for the repository.")
        return docs

    except GithubException as e:
        raise ValueError(f"Failed to fetch repository '{repo_path}': {e.data.get('message', 'Check URL and token permissions.')}")
    except Exception as e:
        raise ValueError(f"An unexpected error occurred: {str(e)}")


def fetch_repo_tree(repo_url):
    """
    Fetches the repository structure as a tree for UI display.
    Returns a hierarchical structure with file/folder information.
    """
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    if not GITHUB_TOKEN:
        raise ValueError("GitHub token not set. Please set the GITHUB_TOKEN environment variable.")

    try:
        g = Github(GITHUB_TOKEN)
        repo_path = repo_url.replace('https://github.com/', '').strip('/')
        repo = g.get_repo(repo_path)

        print(f"Building tree for: {repo_path}")
        
        tree_data = {
            "name": repo.name,
            "type": "repository",
            "path": "",
            "children": _build_tree_structure(repo)
        }
        
        return tree_data

    except GithubException as e:
        raise ValueError(f"Failed to fetch repository '{repo_path}': {e.data.get('message', 'Check URL and token permissions.')}")
    except Exception as e:
        raise ValueError(f"An unexpected error occurred: {str(e)}")


def _build_tree_structure(repo, path=""):
    """Helper to recursively build a hierarchical tree structure."""
    tree_items = []
    binary_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.ico', '.zip', '.pdf', '.woff', '.woff2', '.DS_Store']
    
    try:
        contents = repo.get_contents(path)
        
        directories = sorted([c for c in contents if c.type == "dir"], key=lambda x: x.name.lower())
        files = sorted([c for c in contents if c.type == "file"], key=lambda x: x.name.lower())
        
        for content in directories:
            tree_items.append({
                "name": content.name,
                "type": "directory",
                "path": content.path,
                "children": _build_tree_structure(repo, content.path)
            })
        
        for content in files:
            is_binary = any(content.name.lower().endswith(ext) for ext in binary_extensions)
            is_large = content.size > 100000
            
            tree_items.append({
                "name": content.name,
                "type": "file",
                "path": content.path,
                "size": content.size,
                "is_binary": is_binary,
                "is_large": is_large,
                "viewable": not (is_binary or is_large)
            })
            
    except GithubException as e:
        print(f"Could not list contents of {path}: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while building tree for {path}: {e}")
        
    return tree_items


def fetch_file_content(repo_url, file_path):
    """
    Fetches the content of a specific file from the repository.
    """
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    if not GITHUB_TOKEN:
        raise ValueError("GitHub token not set. Please set the GITHUB_TOKEN environment variable.")

    try:
        g = Github(GITHUB_TOKEN)
        repo_path = repo_url.replace('https://github.com/', '').strip('/')
        repo = g.get_repo(repo_path)

        print(f"Fetching file content for: {file_path}")
        
        file_content = repo.get_contents(file_path)
        
        binary_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.ico', '.zip', '.pdf', '.woff', '.woff2', '.DS_Store']
        is_binary = any(file_path.lower().endswith(ext) for ext in binary_extensions)
        is_large = file_content.size > 500000
        
        if is_binary:
            return {"path": file_path, "content": None, "error": "Binary file - content not displayable", "is_binary": True, "size": file_content.size}
        
        if is_large:
            return {"path": file_path, "content": None, "error": "File too large to display", "is_binary": False, "size": file_content.size}
        
        try:
            content = file_content.decoded_content.decode('utf-8')
            return {"path": file_path, "content": content, "error": None, "is_binary": False, "size": file_content.size}
        except UnicodeDecodeError:
            return {"path": file_path, "content": None, "error": "File contains non-UTF-8 content and cannot be displayed", "is_binary": True, "size": file_content.size}

    except GithubException as e:
        raise ValueError(f"Failed to fetch file '{file_path}': {e.data.get('message', 'File not found or access denied.')}")
    except Exception as e:
        raise ValueError(f"An unexpected error occurred: {str(e)}")


def _get_repo_structure(repo, path="", indent=""):
    """Helper to recursively build a text representation of the repo structure."""
    structure = ""
    try:
        contents = repo.get_contents(path)
        for content in contents:
            if content.type == "dir":
                structure += f"{indent}📁 {content.name}/\n"
                structure += _get_repo_structure(repo, content.path, indent + "  ")
            else:
                structure += f"{indent}📄 {content.name}\n"
    except GithubException:
        pass
    except Exception as e:
        print(f"Could not list contents of {path}: {e}")
    return structure


def process_uploaded_file_docs(file_stream):
    """
    Processes a .md or .txt file from an in-memory stream.
    """
    filename = file_stream.filename
    print(f"Processing uploaded file stream: {filename}")
    
    raw_text = ""
    if filename.lower().endswith(('.md', '.txt')):
        raw_text = file_stream.read().decode('utf-8')
    else:
        # This function should not be called for other types
        raise ValueError("Unsupported file type for this function.")
    
    if not raw_text.strip():
        return []

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=4000, chunk_overlap=200)
    chunks = text_splitter.split_text(raw_text)
    
    docs = []
    for i, chunk in enumerate(chunks):
        docs.append(Document(
            page_content=chunk,
            metadata={"source": filename, "chunk": i}
        ))
        
    print(f"Created {len(docs)} documents for {filename}.")
    return docs

def process_pdf_file_and_chunk(file_stream):
    """
    Processes a PDF file from an in-memory stream.
    """
    filename = file_stream.filename
    print(f"Processing PDF stream: {filename}")

    raw_text = ""
    try:
        # Read PDF content from the in-memory stream
        pdf_reader = PdfReader(BytesIO(file_stream.read()))
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:
                raw_text += page_text
    except Exception as e:
        raise ValueError(f"Could not read the provided PDF stream: {e}")

    if not raw_text.strip():
        return []

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=3000, chunk_overlap=150)
    chunks = text_splitter.split_text(raw_text)

    pdf_docs = []
    for i, chunk_content in enumerate(chunks):
        pdf_docs.append(Document(
            page_content=chunk_content,
            metadata={"source": filename, "chunk_id": i}
        ))

    print(f"Successfully created {len(pdf_docs)} chunks for {filename}.")
    return pdf_docs