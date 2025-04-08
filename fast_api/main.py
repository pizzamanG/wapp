from fastapi import FastAPI, Request, Form, Depends, HTTPException, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

import os
import bcrypt
import jwt
import datetime

from sqlalchemy import create_engine, Column, Integer, String, DateTime, exists
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# ----- CONFIGURATION -----
SECRET_KEY = "your-secret-key"  # Replace with a secure key in production!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Filesystem & DB settings
BASE_DIR = "/media/storagebox/george/labelling_21_03_2025/labelling/extracted_frames_07_apr_2025"
DATABASE_URL = "postgresql://myuser:mypassword@localhost/mydb"

# ----- SETUP APP, DATABASE, TEMPLATES -----
app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

# ----- MODELS -----
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Selection(Base):
    __tablename__ = "selections"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False)
    category = Column(String, nullable=False)
    video_id = Column(String, nullable=False)
    image_path = Column(String, nullable=False)
    rank = Column(Integer, nullable=False)  # 1, 2, or 3
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ----- DEPENDENCIES & UTILS -----
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_access_token(data: dict, expires_delta: datetime.timedelta = None):
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + (expires_delta or datetime.timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        return username
    except jwt.PyJWTError:
         raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    username = verify_token(token)
    user = db.query(User).filter(User.username == username).first()
    if user is None:
         raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

# ----- DATABASE OPERATIONS -----
def get_user_by_username(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()

def create_new_user(db: Session, username: str, password: str):
    if db.query(exists().where(User.username == username)).scalar():
         return False, "Username already exists."
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(username=username, password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return True, "Account created."

def check_user_password(db: Session, username: str, password: str):
    user = get_user_by_username(db, username)
    if user and bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return True
    return False

def save_selection_to_db(db: Session, username, category, video_id, ranked_selection: dict):
    for rank, frame in ranked_selection.items():
         image_path = os.path.join(BASE_DIR, category, video_id, frame)
         selection = Selection(
             username=username,
             category=category,
             video_id=video_id,
             image_path=image_path,
             rank=int(rank)
         )
         db.add(selection)
    db.commit()

def get_labeled_videos_for_user(db: Session, username, category):
    result = db.query(Selection.video_id).filter(Selection.username==username, Selection.category==category).distinct().all()
    return set(row[0] for row in result)

# ----- FILESYSTEM HELPERS -----
def list_categories(base_dir):
    return sorted([d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))])

def list_videos(category_path):
    return sorted([d for d in os.listdir(category_path) if os.path.isdir(os.path.join(category_path, d))])

def list_frames(video_path):
    return sorted([f for f in os.listdir(video_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])

# ----- ROUTES -----
# Root: if logged in, show main app; otherwise, show login page.
@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    try:
       # Check authentication by attempting to retrieve user.
       user = get_current_user(request)
       return templates.TemplateResponse("index.html", {"request": request, "username": user.username})
    except Exception:
       return templates.TemplateResponse("login.html", {"request": request, "error": ""})

# Login endpoint
@app.post("/login")
def login(response: Response, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if check_user_password(db, username, password):
         access_token = create_access_token(data={"sub": username}, expires_delta=datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
         response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
         response.set_cookie(key="access_token", value=access_token, httponly=True)
         return response
    else:
         return templates.TemplateResponse("login.html", {"request": Request, "error": "Invalid credentials"})

# Register endpoint
@app.post("/register")
def register(response: Response, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    success, message = create_new_user(db, username, password)
    if success:
         access_token = create_access_token(data={"sub": username}, expires_delta=datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
         response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
         response.set_cookie(key="access_token", value=access_token, httponly=True)
         return response
    else:
         return templates.TemplateResponse("login.html", {"request": Request, "error": message})

# API endpoint: list categories
@app.get("/api/categories")
def api_categories(user=Depends(get_current_user)):
    categories = list_categories(BASE_DIR)
    return {"categories": categories}

# API endpoint: list videos (exclude already labeled)
@app.get("/api/videos")
def api_videos(category: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    category_path = os.path.join(BASE_DIR, category)
    all_videos = list_videos(category_path)
    labeled = get_labeled_videos_for_user(db, user.username, category)
    videos = [v for v in all_videos if v not in labeled]
    return {"videos": videos}

# API endpoint: list frames
@app.get("/api/frames")
def api_frames(category: str, video: str, user=Depends(get_current_user)):
    video_path = os.path.join(BASE_DIR, category, video)
    frames = list_frames(video_path)
    return {"frames": frames, "video": video, "category": category}

# API endpoint: save selection
@app.post("/api/selection")
def api_selection(category: str, video_id: str, selection: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if len(selection) != 3:
         raise HTTPException(status_code=400, detail="Selection must include exactly 3 ranks.")
    save_selection_to_db(db, user.username, category, video_id, selection)
    return {"message": "Selection saved."}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
