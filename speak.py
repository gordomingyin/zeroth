#!/usr/bin/env python3
"""Zeroth voice: text -> spoken MP3. Usage: speak.py "text" [--voice V] [--out PATH]"""
import sys, subprocess
text = sys.argv[1]
voice = sys.argv[sys.argv.index('--voice')+1] if '--voice' in sys.argv else 'avocado_v2:hubert'
out = sys.argv[sys.argv.index('--out')+1] if '--out' in sys.argv else 'zeroth_said.mp3'
p = subprocess.run(['/opt/hatch/bin/tts','speak','--voice',voice,'--output',out,'--text-stdin'],
                   input=text.encode(), capture_output=True)
print(p.stdout.decode()[-200:] if p.returncode==0 else p.stderr.decode()[-500:])
