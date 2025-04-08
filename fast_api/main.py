from fastapi import FastAPI, Request, Form, Depends, HTTPException, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

import os
import bcrypt
import jwt
import datetime
import secrets
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from passlib.context import CryptContext
import csv
from io import StringIO
from fastapi.responses import StreamingResponse

from sqlalchemy import create_engine, Column, Integer, String, DateTime, exists, ForeignKey, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from typing import List, Optional

# ----- CONFIGURATION -----
SECRET_KEY = "your-secret-key"  # Replace with a secure key in production!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Filesystem & DB settings
BASE_DIR = "/media/storagebox/george/labelling_21_03_2025/labelling/extracted_frames_07_apr_2025"
DATABASE_URL = "postgresql://myappuser:mypassword@localhost/myappdb"

# ----- SETUP APP, DATABASE, TEMPLATES -----
app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ----- MODELS -----
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    selections = relationship("Selection", back_populates="user")

class Selection(Base):
    __tablename__ = "selections"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    category = Column(String, nullable=False)
    video_id = Column(String, nullable=False)
    frame_filename = Column(String, index=True)
    rank = Column(Integer, nullable=False)  # 1, 2, or 3
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    
    user = relationship("User", back_populates="selections")

Base.metadata.create_all(bind=engine)

# ----- DEPENDENCIES & UTILS -----
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def authenticate_user(db, username: str, password: str):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return False
    if not verify_password(password, user.password_hash):
        return False
    return user

def get_current_user(request: Request, db: Session = Depends(get_db)):
    username = request.session.get("username")
    if username is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# ----- FILESYSTEM HELPERS -----
def list_categories(base_dir):
    return sorted([d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))])

def list_videos(category):
    category_path = os.path.join(BASE_DIR, category)
    return sorted([d for d in os.listdir(category_path) if os.path.isdir(os.path.join(category_path, d))])

def list_frames(video_path):
    return sorted([f for f in os.listdir(video_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])

# ----- DATABASE OPERATIONS -----
def get_labeled_videos_for_user(db, username, category):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return []
    
    # Get distinct video_ids where the user has made selections
    selections = db.query(Selection.video_id).filter(
        Selection.user_id == user.id,
        Selection.category == category
    ).distinct().all()
    
    return [selection[0] for selection in selections]

def save_selection_to_db(db, username, category, video_id, frame_filename, rank):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if this rank already exists for this video
    existing_selection = db.query(Selection).filter(
        Selection.user_id == user.id,
        Selection.category == category,
        Selection.video_id == video_id,
        Selection.rank == rank
    ).first()
    
    if existing_selection:
        # Update existing selection
        existing_selection.frame_filename = frame_filename
    else:
        # Create new selection
        new_selection = Selection(
            user_id=user.id,
            category=category,
            video_id=video_id,
            frame_filename=frame_filename,
            rank=rank
        )
        db.add(new_selection)
    
    db.commit()
    return {"success": True}

def get_user_selections(db, username, category, video_id):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return {}
    
    selections = db.query(Selection).filter(
        Selection.user_id == user.id,
        Selection.category == category,
        Selection.video_id == video_id
    ).all()
    
    result = {}
    for selection in selections:
        result[selection.rank] = selection.frame_filename
    
    return result

# ----- ROUTES -----
# Session middleware
@app.middleware("http")
async def session_middleware(request: Request, call_next):
    if not hasattr(request, "session"):
        request.session = {}
    response = await call_next(request)
    return response

@app.get("/", response_class=HTMLResponse)
async def root(request: Request, db: Session = Depends(get_db)):
    if "username" not in request.session:
        return RedirectResponse(url="/login")
    
    categories = list_categories(BASE_DIR)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "username": request.session.get("username"),
        "categories": categories
    })

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid username or password"
        })
    
    request.session["username"] = user.username
    return RedirectResponse(url="/", status_code=303)

@app.post("/register")
async def register(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    # Check if username already exists
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Username already exists"
        })
    
    # Create new user
    hashed_password = get_password_hash(password)
    new_user = User(username=username, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    
    request.session["username"] = username
    return RedirectResponse(url="/", status_code=303)

@app.get("/logout")
async def logout(request: Request):
    request.session.pop("username", None)
    return RedirectResponse(url="/login")

@app.get("/category/{category}", response_class=HTMLResponse)
async def view_category(request: Request, category: str, db: Session = Depends(get_db)):
    if "username" not in request.session:
        return RedirectResponse(url="/login")
    
    videos = list_videos(category)
    labeled_videos = get_labeled_videos_for_user(db, request.session["username"], category)
    
    return templates.TemplateResponse("category.html", {
        "request": request,
        "username": request.session.get("username"),
        "category": category,
        "videos": videos,
        "labeled_videos": labeled_videos
    })

@app.get("/video/{category}/{video_id}", response_class=HTMLResponse)
async def view_video(request: Request, category: str, video_id: str, db: Session = Depends(get_db)):
    if "username" not in request.session:
        return RedirectResponse(url="/login")
    
    video_path = os.path.join(BASE_DIR, category, video_id)
    frames = list_frames(video_path)
    
    # Get user's existing selections for this video
    selections = get_user_selections(db, request.session["username"], category, video_id)
    
    return templates.TemplateResponse("video.html", {
        "request": request,
        "username": request.session.get("username"),
        "category": category,
        "video_id": video_id,
        "frames": frames,
        "selections": selections
    })

@app.get("/frame/{category}/{video_id}/{frame}")
async def get_frame(category: str, video_id: str, frame: str):
    frame_path = os.path.join(BASE_DIR, category, video_id, frame)
    return FileResponse(frame_path)

# API endpoints
@app.get("/api/categories")
def api_categories():
    categories = list_categories(BASE_DIR)
    return {"categories": categories}

@app.get("/api/videos")
def api_videos(category: str):
    videos = list_videos(category)
    return {"videos": videos}

@app.get("/api/unlabeled_videos")
def api_unlabeled_videos(category: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    all_videos = list_videos(category)
    labeled = get_labeled_videos_for_user(db, user.username, category)
    videos = [v for v in all_videos if v not in labeled]
    return {"videos": videos}

@app.get("/api/frames")
def api_frames(category: str, video_id: str):
    video_path = os.path.join(BASE_DIR, category, video_id)
    frames = list_frames(video_path)
    return {"frames": frames, "video": video_id, "category": category}

@app.get("/api/selections")
def api_get_selections(category: str, video_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    selections = get_user_selections(db, user.username, category, video_id)
    return {"selections": selections}

@app.post("/api/selection")
def api_selection(
    category: str, 
    video_id: str, 
    frame_filename: str, 
    rank: int,
    user=Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    if rank not in [1, 2, 3]:
        raise HTTPException(status_code=400, detail="Rank must be 1, 2, or 3")
    
    save_selection_to_db(db, user.username, category, video_id, frame_filename, rank)
    return {"message": "Selection saved."}

@app.delete("/api/selection")
def api_delete_selection(
    category: str, 
    video_id: str, 
    rank: int,
    user=Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    if rank not in [1, 2, 3]:
        raise HTTPException(status_code=400, detail="Rank must be 1, 2, or 3")
    
    user_obj = db.query(User).filter(User.username == user.username).first()
    if not user_obj:
        raise HTTPException(status_code=404, detail="User not found")
    
    selection = db.query(Selection).filter(
        Selection.user_id == user_obj.id,
        Selection.category == category,
        Selection.video_id == video_id,
        Selection.rank == rank
    ).first()
    
    if selection:
        db.delete(selection)
        db.commit()
        return {"message": "Selection deleted."}
    else:
        raise HTTPException(status_code=404, detail="Selection not found")

@app.get("/export", response_class=HTMLResponse)
async def export_page(request: Request, db: Session = Depends(get_db)):
    if "username" not in request.session:
        return RedirectResponse(url="/login")
    
    # Get all users for the filter dropdown
    users = db.query(User.username).all()
    users = [user[0] for user in users]
    
    # Get all categories
    categories = list_categories(BASE_DIR)
    
    return templates.TemplateResponse("export.html", {
        "request": request,
        "username": request.session.get("username"),
        "users": users,
        "categories": categories
    })

@app.get("/api/export")
def api_export(
    username: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db)
):
    # Build query
    query = db.query(
        User.username,
        Selection.category,
        Selection.video_id,
        Selection.frame_filename,
        Selection.rank
    ).join(User)
    
    # Apply filters
    if username:
        query = query.filter(User.username == username)
    if category:
        query = query.filter(Selection.category == category)
    
    # Execute query
    results = query.all()
    
    # Create CSV
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['labeller_username', 'category', 'video_id', 'frame_filename', 'rank'])
    
    for row in results:
        writer.writerow(row)
    
    # Return CSV as download
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=export.csv"}
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
