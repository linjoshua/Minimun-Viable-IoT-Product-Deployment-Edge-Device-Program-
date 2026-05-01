"""
web_viewer
==========

Web-only real-time audio streaming viewer.

- Captures mic audio via GStreamer WASAPI (reusing wav_saver modules)
- Streams PCM S16LE chunks over WebSocket to web.html (AudioWorklet playback)
- No WAV rolling / no disk writing

Copyright
---------
© 2026 Yuseop Sim. All rights reserved.

Author
------
Yuseop Sim
Ph.D. Student, Smart Manufacturing Laboratory (MMRL)
Purdue University

Import:!!! enable port on Window!!! 

"""