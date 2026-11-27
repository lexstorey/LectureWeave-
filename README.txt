LECTURE TRANSCRIPT + SLIDES TOOL
================================

What it does
------------
This tool finds stable slide changes in a lecture recording, transcribes the
audio locally with Faster Whisper, matches each piece of speech to the slide
that was visible, and creates a Word document.

Nothing is uploaded to Microsoft or another transcription service.

One-time setup
--------------
1. Install 64-bit Python 3.11 or newer from https://www.python.org/downloads/
   and select "Add Python to PATH" during installation.
2. Double-click Setup.cmd.
3. Wait for the packages to install.

Creating a document
-------------------
Drag an MP4 onto "Create Transcript.cmd".

Alternatively, double-click "Create Transcript.cmd" and paste the full path
to the video when prompted.

The Word document is written beside the video as:
    <video filename> Transcript and Slides.docx

First-run notes
---------------
* The first transcription downloads the Whisper "turbo" model. It needs
  roughly 1.6 GB of free disk space.
* NVIDIA acceleration is attempted automatically. If the required CUDA
  libraries are unavailable, the tool falls back to CPU processing.
* A one-hour recording can take a while on CPU, but no transcription credits
  are used.
* Models and temporary processing files are cached under:
    %LOCALAPPDATA%\LectureTranscriptTool

Accuracy
--------
The transcript is machine-generated. Check quotations, names, case citations,
statutory provisions and other precise legal wording against the recording.

Only process lecture recordings that you are authorised to download and use.

