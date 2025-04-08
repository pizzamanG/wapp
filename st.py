import streamlit as st
import os
import bcrypt
from PIL import Image
import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, and_, exists
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# ======= CONFIGURATION =======
BASE_DIR = "/media/storagebox/george/labelling_21_03_2025/labelling/extracted_frames_07_apr_2025"
DATABASE_URL = "postgresql://myuser:mypassword@localhost/mydb"

# ======= SQLAlchemy Setup =======
Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

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

# ======= DB Functions =======
def get_user(username):
    session = SessionLocal()
    try:
        return session.query(User).filter_by(username=username).first()
    finally:
        session.close()

def create_user(username, password):
    session = SessionLocal()
    try:
        if session.query(exists().where(User.username == username)).scalar():
            return False, "Username already exists."
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user = User(username=username, password_hash=password_hash)
        session.add(user)
        session.commit()
        return True, "Account created."
    except Exception as e:
        session.rollback()
        return False, str(e)
    finally:
        session.close()

def check_password(username, password):
    user = get_user(username)
    if user and bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return True
    return False

def save_selection_to_db(username, category, video_id, ranked_selection):
    session = SessionLocal()
    try:
        for rank, frame in ranked_selection.items():
            image_path = os.path.join(BASE_DIR, category, video_id, frame)
            selection = Selection(
                username=username,
                category=category,
                video_id=video_id,
                image_path=image_path,
                rank=rank
            )
            session.add(selection)
        session.commit()
        st.success("✅ Selections saved.")
    except Exception as e:
        session.rollback()
        st.error(f"Database error: {e}")
    finally:
        session.close()

def get_labeled_videos_for_user(username, category):
    session = SessionLocal()
    try:
        result = session.query(Selection.video_id).filter_by(username=username, category=category).distinct().all()
        return set(row.video_id for row in result)
    finally:
        session.close()

# ======= Filesystem Helpers =======
def list_categories(base_dir):
    return sorted([d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))])

def list_videos(category_path):
    return sorted([d for d in os.listdir(category_path) if os.path.isdir(os.path.join(category_path, d))])

def list_frames(video_path):
    return sorted([f for f in os.listdir(video_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])

# ======= Login & Registration =======
def show_login():
    st.title("Login or Register")
    mode = st.radio("Choose an option", ["Login", "Register"])
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button(mode):
        if mode == "Register":
            success, msg = create_user(username, password)
            if success:
                st.success(msg)
            else:
                st.error(msg)
        elif mode == "Login":
            if check_password(username, password):
                st.session_state.logged_in = True
                st.session_state.username = username
                st.success(f"Welcome, {username}!")
            else:
                st.error("Invalid username or password.")

# ======= Main App =======
def main_app():
    st.title("Thumbnail Selector")
    st.write("Assign ranks 1, 2, or 3 to your top image choices.")

    categories = list_categories(BASE_DIR)
    if not categories:
        st.error("No categories found.")
        return

    selected_category = st.sidebar.selectbox("Category", categories)
    category_path = os.path.join(BASE_DIR, selected_category)

    all_videos = list_videos(category_path)
    labeled = get_labeled_videos_for_user(st.session_state.username, selected_category)
    videos = [v for v in all_videos if v not in labeled]

    if not videos:
        st.success("🎉 All videos labeled in this category!")
        return

    if "video_index" not in st.session_state:
        st.session_state.video_index = 0

    current_video = videos[st.session_state.video_index]
    video_path = os.path.join(category_path, current_video)
    frames = list_frames(video_path)

    st.subheader(f"Category: {selected_category} | Video: {current_video}")

    if "ranked_selection" not in st.session_state:
        st.session_state.ranked_selection = {}

    st.markdown("""
        <style>
        .rank-badge {
            position: absolute;
            top: 10px;
            right: 10px;
            background-color: red;
            color: white;
            border-radius: 50%;
            width: 24px;
            height: 24px;
            text-align: center;
            line-height: 24px;
            font-weight: bold;
        }
        </style>
    """, unsafe_allow_html=True)

    cols = st.columns(3)
    for i, frame in enumerate(frames):
        image_path = os.path.join(video_path, frame)
        col = cols[i % 3]
        with col:
            img = Image.open(image_path)
            img.thumbnail((300, 300))
            if any(frame == f for f in st.session_state.ranked_selection.values()):
                st.image(img, use_container_width=True)
            else:
                st.markdown('<div style="opacity: 0.3">', unsafe_allow_html=True)
                st.image(img, use_container_width=True)
                st.markdown('</div>', unsafe_allow_html=True)

            current_rank = None
            for rank, selected_frame in st.session_state.ranked_selection.items():
                if selected_frame == frame:
                    current_rank = rank
                    break

            rank_options = ["None"] + ["1", "2", "3"]
            index = 0
            if current_rank:
                index = rank_options.index(str(current_rank))

            selected_rank = st.radio("", rank_options, index=index if index is not None else 0, key=f"{current_video}_{frame}", horizontal=True)

            if selected_rank != "None":
                selected_rank = int(selected_rank)
                # Remove current rank if already used or frame already selected
                for r in list(st.session_state.ranked_selection.keys()):
                    if st.session_state.ranked_selection.get(r) == frame:
                        del st.session_state.ranked_selection[r]
                for r, f in list(st.session_state.ranked_selection.items()):
                    if r == selected_rank:
                        del st.session_state.ranked_selection[r]
                st.session_state.ranked_selection[selected_rank] = frame
            else:
                for r in list(st.session_state.ranked_selection.keys()):
                    if st.session_state.ranked_selection[r] == frame:
                        del st.session_state.ranked_selection[r]

            # Display badge
            for r, f in st.session_state.ranked_selection.items():
                if f == frame:
                    st.markdown(f'<div class="rank-badge">{r}</div>', unsafe_allow_html=True)

    selected = st.session_state.ranked_selection
    if len(selected) != 3:
        st.sidebar.warning("Please assign exactly one frame to each rank: 1, 2, and 3.")
    if st.sidebar.button("Save and Next Video"):
        if len(selected) == 3:
            with st.spinner("Saving and loading next video..."):
                save_selection_to_db(
                    st.session_state.username,
                    selected_category,
                    current_video,
                    selected
                )
                st.session_state.video_index = min(len(videos) - 1, st.session_state.video_index + 1)
                st.session_state.ranked_selection = {}
                st.experimental_rerun()
        else:
            st.sidebar.error("Selection incomplete. You must assign 3 unique ranks.")

# ======= Entrypoint =======
def main():
    if "logged_in" not in st.session_state or not st.session_state.logged_in:
        show_login()
    else:
        main_app()

if __name__ == "__main__":
    main()






see how this uses Postgres 

I wanna refactor it as a better tool, perhaps with fast API and a simple but effective frontend
