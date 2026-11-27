from cx_Freeze import Executable, setup


build_exe_options = {
    "build_exe": "build_cx/Lecture Transcript and Slides",
    "include_msvcr": True,
    "packages": [
        "PySide6",
        "av",
        "ctranslate2",
        "cv2",
        "docx",
        "faster_whisper",
        "huggingface_hub",
        "numpy",
        "onnxruntime",
        "PIL",
        "tokenizers",
    ],
    "excludes": ["tkinter", "tkinterdnd2"],
    "optimize": 1,
}


setup(
    name="Lecture Transcript and Slides",
    version="1.0.0",
    description="Create a timestamped lecture transcript with slides and chat comments.",
    options={"build_exe": build_exe_options},
    executables=[
        Executable(
            "lecture_transcript_gui_qt.py",
            base="gui",
            target_name="Lecture Transcript and Slides.exe",
        )
    ],
)

