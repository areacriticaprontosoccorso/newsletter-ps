"""
EM Weekly Digest — Versione con lista destinatari
Pronto Soccorso San Giovanni Bosco, Torino

Comando: python newsletter_rss.py
"""

import os
import re
import json
import time
import logging
import smtplib
import base64
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

import config as cfg

DESTINATARI = [
    "francesco.panero@aslcittaditorino.it",
    "S.C.Medicinad'EmergenzaeUrgenza@aslcittaditorino.it",
]

TIPI_ESCLUSI = [
    "Case Reports", "Letter", "Comment", "Editorial Comment",
    "Published Erratum", "Correction", "Retraction", "News",
    "Biography", "Personal Narrative",
]

logging.basicConfig(
    level=logging.INFO,
