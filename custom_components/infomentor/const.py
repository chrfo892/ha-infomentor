"""Constants for the InfoMentor integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "infomentor"

CONF_SCAN_MINUTES = "scan_minutes"
DEFAULT_SCAN_MINUTES = 30
MIN_SCAN_MINUTES = 10

CONF_MODULES = "modules"

CONF_AUTO_DOWNLOAD = "auto_download"
CONF_DOWNLOAD_PATH = "download_path"
CONF_PREP_LESSON_KEYWORDS = "prep_lesson_keywords"
DEFAULT_DOWNLOAD_PATH = "/media/infomentor"
DEFAULT_PREP_LESSON_KEYWORDS = ["IDH", "Idrott", "SL", "Träslöjd", "Traslojd"]

MODULE_TIMETABLE = "timetable"
MODULE_CALENDAR = "calendar"
MODULE_NOTIFICATIONS = "notifications"
MODULE_ATTENDANCE = "attendance"
MODULE_LEARNLOG = "learnlog"
MODULE_TIMEREGISTRATION = "timeregistration"

ALL_MODULES = [
    MODULE_TIMETABLE,
    MODULE_CALENDAR,
    MODULE_NOTIFICATIONS,
    MODULE_ATTENDANCE,
    MODULE_LEARNLOG,
    MODULE_TIMEREGISTRATION,
]

MODULE_LABELS = {
    MODULE_TIMETABLE: "Timetable (next lesson, schedule)",
    MODULE_CALENDAR: "Calendar (events, weekly letters)",
    MODULE_NOTIFICATIONS: "Notifications",
    MODULE_ATTENDANCE: "Attendance / absences",
    MODULE_LEARNLOG: "Learnlog (posts and photos)",
    MODULE_TIMEREGISTRATION: "Time registration (check in/out, comments)",
}

DEFAULT_SCAN_INTERVAL = timedelta(minutes=DEFAULT_SCAN_MINUTES)

EVENT_NEW_LEARNLOG_MEDIA = f"{DOMAIN}_new_learnlog_media"
EVENT_NEW_CALENDAR_ATTACHMENT = f"{DOMAIN}_new_calendar_attachment"

SERVICE_DOWNLOAD_FILE = "download_file"
SERVICE_DOWNLOAD_BACKLOG = "download_backlog"
SERVICE_SET_TIME_REGISTRATION_COMMENT = "set_time_registration_comment"
SERVICE_GET_LEARNLOG_POSTS = "get_learnlog_posts"

ATTR_FILE_ID = "file_id"
ATTR_PATH = "path"
ATTR_FILENAME = "filename"
ATTR_PUPIL_ID = "pupil_id"
ATTR_DEVICE_ID = "device_id"
ATTR_START_DATE = "start_date"
ATTR_END_DATE = "end_date"
ATTR_DATE = "date"
ATTR_COMMENT = "comment"
ATTR_GO_HOME_TIME = "go_home_time"
ATTR_SOURCES = "sources"
ATTR_LIMIT = "limit"

SOURCE_PHOTOS = "photos"
SOURCE_LETTERS = "letters"

PLATFORMS = ["binary_sensor", "sensor", "calendar"]

STORAGE_KEY = f"{DOMAIN}.seen_files"
STORAGE_VERSION = 1
