import pytest
from routes.candidate_profile_routes import (
    _certifications_from_section,
    _projects_from_section,
    _job_preferences_from_section,
    _skills_from_section,
    _education_from_section,
)

def test_image_2_real_resume_projects_extraction():
    resume_projects_text = """
    PERSONAL PROJECTS
    Vehicle Price Prediction Using Machine Learning
        • Built an end-to-end ML model to predict used vehicle resale prices using a real-time dataset
        • Preprocessed data using IQR outlier capping, log transformation, and binary encoding of safety features
        • Trained and compared Linear Regression, XGBoost using sklearn Pipeline to prevent data leakage
        • Achieved R² score of 0.94 with XGBoost, validated with 5-fold cross-validation for consistent generalization
        • Deployed as an interactive web app using Streamlit for real-time price prediction.
    DeepGuard — Multi-Modal Deepfake & Fraud Detection
        • Built a deepfake detection system combining deep learning, computer vision, and NLP for fraud prevention in digital financial systems
        • Applied transfer learning on EfficientNet-B4 for Real/Fake face classification with probability scoring
        • Implemented real-time liveness detection using MediaPipe FaceMesh with Eye Aspect Ratio blink detection
        • Verified human presence using Eye Aspect Ratio across 6 MediaPipe landmarks, confirming liveness after 2 consecutive blinks to prevent static photo and screen replay attacks.
    DocSense AI — Multimodal RAG Based PDF Question Answering System
        • Built DocSense AI, a Multimodal RAG Chatbot answering PDF-based queries using FAISS vector search and Gemini 2.5 Flash Lite (1M token context window)
        • Implemented PDF chunking pipeline using LangChain (700-char chunks, 100-char overlap) with all-MiniLM-L6-v2 embeddings for semantic search
        • Applied similarity threshold (0.45) and keyword filtering to retrieve top 3 relevant chunks from 8 results, reducing hallucination
        • Delivered multimodal output — text answers with corresponding PDF page images mapped to source page numbers
        • Designed Streamlit UI with prompt engineering to restrict responses strictly to document context, returning "out of context" when unavailable

    EXTRA CURRICULAR ACTIVITIES
    KATTELA HARSHITHA
    CGPA: 8.5
    """
    projs = _projects_from_section(resume_projects_text)
    
    # Must extract EXACTLY 3 projects
    assert len(projs) == 3

    assert projs[0]["title"] == "Vehicle Price Prediction Using Machine Learning"
    assert "Built an end-to-end ML model" in projs[0]["description"]
    assert "Preprocessed data using IQR" in projs[0]["description"]
    assert "Preprocessed data" not in projs[0]["title"]

    assert "DeepGuard" in projs[1]["title"]
    assert "Built a deepfake detection system" in projs[1]["description"]

    assert "DocSense AI" in projs[2]["title"]
    assert "Built DocSense AI" in projs[2]["description"]

def test_missing_projects_section_returns_empty():
    assert _projects_from_section("") == []
    assert _projects_from_section(None) == []
