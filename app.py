import yt_dlp
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import os
import asyncio
import logging
import json

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Comprehensive Video Downloader API",
    description="API to download videos from various platforms using yt-dlp.",
    version="1.0.0"
)

# --- Configuration ---
DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True) # Ensure downloads directory exists

# --- Pydantic Models for Request Bodies ---

class YtDlpOptions(BaseModel):
    """
    Model for custom yt-dlp options.
    Refer to yt-dlp documentation for available options:
    https://github.com/yt-dlp/yt-dlp#api-example
    """
    # Example options, you can add more as needed
    format: str = Field("bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                        description="Desired video format. Default prioritizes MP4.")
    outtmpl: str = Field(os.path.join(DOWNLOADS_DIR, '%(title)s.%(ext)s'),
                         description="Output template for filename and path.")
    noplaylist: bool = Field(True, description="Download only single video, not playlists.")
    nocheckcertificate: bool = Field(True, description="Ignore SSL/TLS certificates (use with caution).")
    retries: int = Field(5, description="Number of retries for download errors.")
    extractor_retries: int = Field(5, description="Number of retries for extractor errors.")
    verbose: bool = Field(False, description="Enable verbose yt-dlp output for debugging.")
    # Add a field for raw cookies string (e.g., from browser's 'Copy as cURL' or 'EditThisCookie')
    # WARNING: Passing raw cookies directly in an API is a security risk for public APIs.
    # For local/personal use, it might be acceptable.
    cookies: str | None = Field(None, description="Raw cookie string for authentication (e.g., for Instagram).")
    # You can add more yt-dlp options here as needed, e.g., referer, proxy, etc.
    # Example: proxy: str | None = None

class VideoRequest(BaseModel):
    """
    Base model for video operations.
    """
    url: str = Field(..., description="The URL of the video to process.")
    options: YtDlpOptions = Field(YtDlpOptions(), description="Custom yt-dlp options.")

# --- Helper Function for yt-dlp operations ---
async def run_yt_dlp_operation(url: str, ydl_options: dict, download: bool = False):
    """
    Runs a yt-dlp operation (info extraction or download) in a separate thread.
    """
    # Prepare yt-dlp options
    final_ydl_opts = ydl_options.copy()

    # Handle cookies if provided
    if final_ydl_opts.get('cookies'):
        # yt-dlp expects cookies in a file or via --cookies-from-browser.
        # For simplicity with a raw string, we'll write it to a temporary file.
        # In a real app, manage this securely.
        cookies_str = final_ydl_opts.pop('cookies')
        cookie_file_path = os.path.join(DOWNLOADS_DIR, f"cookies_{os.getpid()}.txt")
        try:
            with open(cookie_file_path, 'w') as f:
                f.write(cookies_str)
            final_ydl_opts['cookiefile'] = cookie_file_path
            logger.info(f"Using cookie file: {cookie_file_path}")
        except Exception as e:
            logger.error(f"Failed to write cookie file: {e}")
            raise HTTPException(status_code=500, detail="Failed to process cookies.")

    # Remove verbose if not explicitly set to True, as it can be noisy in logs
    if not final_ydl_opts.get('verbose'):
        final_ydl_opts.pop('verbose', None)

    # yt-dlp needs to be run in a separate thread because it's a blocking operation
    # FastAPI's async nature means we shouldn't block the event loop.
    try:
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(final_ydl_opts) as ydl:
            if download:
                logger.info(f"Attempting to download: {url} with options: {final_ydl_opts}")
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                filepath = ydl.prepare_filename(info)
                logger.info(f"Download complete for {url}. File: {filepath}")
                return {"message": "Video downloaded successfully!", "title": info.get('title'), "filepath": filepath, "extractor": info.get('extractor')}
            else:
                logger.info(f"Attempting to extract info for: {url} with options: {final_ydl_opts}")
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
                logger.info(f"Info extraction complete for {url}.")
                return {"message": "Metadata extracted successfully!", "info": info}
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp DownloadError for {url}: {e}")
        raise HTTPException(status_code=400, detail=f"Download error: {e}")
    except yt_dlp.utils.ExtractorError as e:
        logger.error(f"yt-dlp ExtractorError for {url}: {e}")
        raise HTTPException(status_code=400, detail=f"Extractor error (e.g., unsupported URL, video unavailable): {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred for {url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred: {e}")
    finally:
        # Clean up temporary cookie file if it was created
        if 'cookiefile' in final_ydl_opts and os.path.exists(final_ydl_opts['cookiefile']):
            try:
                os.remove(final_ydl_opts['cookiefile'])
                logger.info(f"Cleaned up cookie file: {final_ydl_opts['cookiefile']}")
            except Exception as e:
                logger.warning(f"Failed to remove cookie file {final_ydl_opts['cookiefile']}: {e}")

# --- API Endpoints ---

@app.get("/")
async def root():
    """
    Root endpoint for the API.
    """
    return JSONResponse(content={
        "message": "Welcome to the Comprehensive Video Downloader API!",
        "endpoints": {
            "/info": "POST - Get video metadata without downloading.",
            "/download": "POST - Download a video."
        },
        "docs": "/docs (Swagger UI) or /redoc (ReDoc)"
    })

@app.post("/info")
async def get_video_info(request: VideoRequest):
    """
    Retrieves metadata for a given video URL without downloading it.
    """
    return await run_yt_dlp_operation(request.url, request.options.model_dump(exclude_none=True), download=False)

@app.post("/download")
async def download_video(request: VideoRequest):
    """
    Downloads a video from the provided URL.
    """
    # For long-running tasks, you might want to use BackgroundTasks
    # or a separate task queue (e.g., Celery) for production.
    # For this example, we'll await it directly but it runs in a thread.
    return await run_yt_dlp_operation(request.url, request.options.model_dump(exclude_none=True), download=True)

# --- Run the FastAPI application ---
if __name__ == "__main__":
    import uvicorn
    # To run on Termux and access from other devices on your network, use host='0.0.0.0'
    uvicorn.run(app, host="0.0.0.0", port=8000)
