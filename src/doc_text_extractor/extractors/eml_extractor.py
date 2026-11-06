"""Internet .eml emails (and winmail.dat / TNEF): body is the record, attachments
become children.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import email
import email.policy
from pathlib import Path


from ..core.models import Options, Page, Result
from ..core.text_utils import decode_bytes, safe_name
from ._attachment_common import _is_small_inline_image
from .html_extractor import html_to_text




def extract_eml(path: Path, workdir: Path, opts: Options) -> Result:
    res = Result(detected="eml")
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    head = {k: str(msg[k]) for k in ("subject", "from", "to", "cc", "date") if msg[k]}
    res.meta["email"] = head
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        payload = body_part.get_payload(decode=True) or b""
        body = html_to_text(payload) if body_part.get_content_type() == "text/html" else decode_bytes(payload)[0]
    res.pages.append(Page(1, "\n".join(f"{k.title()}: {v}" for k, v in head.items()) + "\n\n" + body, "email"))
    skipped_inline = 0
    for i, part in enumerate(msg.iter_attachments()):
        name = safe_name(part.get_filename() or f"attachment_{i}")
        if part.get_content_type() == "message/rfc822":
            inner = part.get_payload(0) if part.is_multipart() else part.get_payload()
            data = inner.as_bytes() if hasattr(inner, "as_bytes") else bytes(inner)
            subj = inner.get("subject") if hasattr(inner, "get") else None
            name = safe_name((subj or Path(name).stem)[:70]) + ".eml"
        else:
            data = part.get_payload(decode=True) or b""
        if name.lower() == "winmail.dat":
            res.children.extend(_tnef_children(data, workdir, res))
            continue
        if _is_small_inline_image(name, data, opts):
            skipped_inline += 1
            continue
        p = workdir / f"eml{i}_{name}"
        p.write_bytes(data)
        res.children.append((name, p))
    if skipped_inline:
        res.meta["inline_images_skipped"] = skipped_inline
    return res




def _tnef_children(data: bytes, workdir: Path, res: Result) -> list:
    try:
        from tnefparse import TNEF
    except ImportError:
        res.flags.append("winmail_dat_not_parsed(install tnefparse)")
        return []
    out = []
    for i, att in enumerate(TNEF(data).attachments):
        name = safe_name(att.long_filename() or att.name or f"tnef_{i}")
        p = workdir / f"tnef{i}_{name}"
        p.write_bytes(att.data)
        out.append((name, p))
    return out