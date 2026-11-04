"""Everything about the colors of a source video, as one self-contained feature.

    domain.py    how a probed stream's tags are read, and the conversion into Rec.709 SDR (pure)
    files.py     applying it wherever the original's pixels are read, and the matte-QC readers

Kept empty on purpose: `media` imports `color.domain` and `color.files` imports `media`, so importing a submodule must never
import its siblings. Modules here import each other explicitly; other code imports `takeone.color.<module>`.
"""
