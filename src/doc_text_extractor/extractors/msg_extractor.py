"""Outlook .msg emails: headers and body are the record, attachments become children.


Moved unchanged from the single-file extractor.py; see README.md for the package layout.
"""
from __future__ import annotations


import os
import re
from pathlib import Path


from ..core.models import Options, Page, Result
from ..core.text_utils import safe_name
from ._attachment_common import _is_small_inline_image
from .html_extractor import html_to_text
from .rtf_extractor import rtf_to_text_bytes




def extract_msg_file(path: Path, workdir: Path, opts: Options) -> Result:
    import extract_msg
    res = Result(detected="msg")
    try:
        try:
            m = extract_msg.openMsg(str(path), strict=False)
        except Exception as e:
            if type(e).__name__ != "TZError":
                raise
            os.environ["TZ"] = "UTC"                 # tzlocal cannot read names like Asia/Calcutta
            if hasattr(__import__("time"), "tzset"):
                __import__("time").tzset()
            m = extract_msg.openMsg(str(path), strict=False)
    except Exception as e:
        if "encrypt" in str(e).lower():
            res.status = "encrypted"; res.flags.append("smime_encrypted")
            return res
        raise
    try:
        def field(name):
            """Header fields with a wrong declared charset raise; keep going and note it."""
            try:
                v = getattr(m, name, None)
                return str(v) if v is not None else None
            except Exception:
                res.flags.append(f"unreadable_header_field:{name}")
                return None
        head = {"subject": field("subject"), "from": field("sender"), "to": field("to"), "cc": field("cc"), "date": field("date")}
        if all(head.get(k) is None for k in ("from", "to")):
            try:                                            # fall back to the raw transport headers
                raw_head = m.headerText or ""
                for k in ("from", "to", "cc", "date"):
                    mm = re.search(rf"(?im)^{k}:\s*(.+)$", raw_head)
                    if mm and not head.get(k):
                        head[k] = mm.group(1).strip()
            except Exception:
                pass
        body = None
        for attr, conv, flag in (("body", None, None), ("htmlBody", html_to_text, "body_from_html"),
                                 ("rtfBody", rtf_to_text_bytes, "body_from_rtf")):
            try:
                v = getattr(m, attr, None)
            except Exception:
                res.flags.append(f"unreadable_{attr}")
                continue
            if v and (conv is None and str(v).strip() or conv is not None):
                body = conv(v) if conv else v
                if flag:
                    res.flags.append(flag)
                if body and str(body).strip():
                    break
        res.meta["email"] = {k: v for k, v in head.items() if v}
        text = "\n".join(f"{k.title()}: {v}" for k, v in head.items() if v) + "\n\n" + (body or "")
        res.pages.append(Page(1, text, "extract-msg"))
        from extract_msg.enums import AttachmentType
        skipped_inline = 0
        for i, a in enumerate(m.attachments):
            name = (getattr(a, "longFilename", None) or getattr(a, "shortFilename", None) or getattr(a, "displayName", None) or f"attachment_{i}")
            name = safe_name(str(name).replace("\x00", ""))
            atype = getattr(a, "type", AttachmentType.DATA)
            try:
                if atype == AttachmentType.DATA:
                    data = a.data
                    if not isinstance(data, (bytes, bytearray)):
                        continue
                    if _is_small_inline_image(name, data, opts):
                        skipped_inline += 1
                        continue
                    p = workdir / f"msg{i}_{name}"
                    p.write_bytes(data)
                    res.children.append((name, p))
                elif atype in (AttachmentType.MSG, AttachmentType.SIGNED_EMBEDDED):
                    p = workdir / f"msg{i}_{Path(name).stem or 'embedded'}.msg"
                    a.data.export(str(p))
                    res.children.append((Path(name).stem + ".msg", p))
                else:
                    res.flags.append(f"attachment_not_extracted:{atype.name}:{name}")
            except Exception as e:
                res.flags.append(f"attachment_failed:{name}:{type(e).__name__}")
        if skipped_inline:
            res.meta["inline_images_skipped"] = skipped_inline
    finally:
        m.close()
    return res