"""
Vendored DeDRM library for Adobe ADEPT DRM removal.

This directory is a placeholder for the DeDRM decryption modules from
the noDRM/DeDRM_tools project. If your version of knock already handles
DRM removal (most do), you don't need anything here.

To populate this directory (only needed if knock can download but not
decrypt):

    1. Clone the DeDRM tools:
       git clone https://github.com/noDRM/DeDRM_tools.git /tmp/dedrm

    2. Copy the needed modules:
       cp /tmp/dedrm/DeDRM_plugin/adobekey.py dedrm/
       cp /tmp/dedrm/DeDRM_plugin/ineptepub.py dedrm/

    3. Or install via pip instead:
       pip3 install git+https://github.com/noDRM/DeDRM_tools.git
"""
