"""Reusable bilibili_worker fixtures, extracted without collecting test cases."""

import asyncio
import html
import json
import sys
import uuid
from types import SimpleNamespace
from urllib.parse import urlencode

from omni.adapters.workers.bilibili import PageForms, PublicationGate
from omni.domain.bilibili import forms_value
from omni.domain.contracts import Effect, Outcome
from omni_test_support.v2.bilibili import BV, FORM, MENTIONS, TEXT, URL

HTML = """<!doctype html><meta charset=utf-8><style>#editor,.body{white-space:pre-wrap}#editor{min-height:40px;border:1px solid}</style>
<a id=account href="https://space.bilibili.com/456" data-mid=456>owner</a>
<div id=challenge hidden></div><div id=loggedout hidden></div>
<section id=target data-id=BV1xx411c7mD><div id=editor contenteditable=true></div><button id=submit>Publish</button><div id=records><!--SERVER_RECORDS--></div></section>
<input id=mention-query><div id=choices></div>
<script>
window.submitted=0;
document.querySelector('#mention-query').addEventListener('input',()=>{
 const choices=document.querySelector('#choices');choices.replaceChildren();
 for(const uid of ['111','222']){const option=document.createElement('button');option.className='candidate';option.dataset.mid=uid;option.textContent='Alice';
 option.onclick=()=>{const editor=document.querySelector('#editor');editor.textContent=editor.textContent.replace(/@$/,'');const chip=document.createElement('span');chip.className='mention';chip.dataset.mid=uid;chip.contentEditable='false';chip.textContent='@Alice';editor.append(chip);choices.replaceChildren();editor.focus();const r=document.createRange();r.selectNodeContents(editor);r.collapse(false);getSelection().removeAllRanges();getSelection().addRange(r);};choices.append(option);}
});
document.querySelector('#editor').addEventListener('keydown',e=>{if(e.key==='Enter')document.querySelector('#submit').click();});
document.querySelector('#submit').onclick=async()=>{
 const editor=document.querySelector('#editor');window.submitted++;
 const mentions=Object.fromEntries([...editor.querySelectorAll('.mention')].map(c=>[c.textContent.slice(1),c.dataset.mid]));
 const data=new URLSearchParams({oid:'123',type:'1',root:'0',parent:'0',message:editor.innerText,at_name_to_mid:JSON.stringify(mentions)});
 const response=await fetch('https://api.bilibili.com/x/v2/reply/add',{method:'POST',body:data});const receipt=await response.json();
 const record=document.createElement('article');record.className='record';record.dataset.id=String(receipt.data.rpid);
 const author=document.createElement('a');author.className='author';author.dataset.mid='456';author.href='https://space.bilibili.com/456';author.textContent='owner';
 const body=document.createElement('div');body.className='body';body.innerHTML=editor.innerHTML;record.append(author,body);document.querySelector('#records').append(record);editor.replaceChildren();
};
</script>"""


def spec():
    return {
        "kind": "comment",
        "url": URL,
        "target_key": BV,
        "remote_target_id": "123",
        "account": "456",
        "form": forms_value({"comment": FORM}, True)["comment"],
    }


def server_record(text=TEXT):
    body = (
        html.escape(text[: MENTIONS[0]["start"]])
        + f'<span class="mention" data-mid="222">@Alice</span>'
        + html.escape(text[MENTIONS[0]["end"] :])
        if text == TEXT
        else html.escape(text)
    )
    return (
        '<article class="record" data-id="99"><a class="author" data-mid="456" href="https://space.bilibili.com/456">owner</a><div class="body">'
        + body
        + "</div></article>"
    )


__all__ = [
    "BV",
    "Effect",
    "FORM",
    "HTML",
    "MENTIONS",
    "Outcome",
    "PageForms",
    "PublicationGate",
    "SimpleNamespace",
    "TEXT",
    "URL",
    "asyncio",
    "forms_value",
    "html",
    "json",
    "server_record",
    "spec",
    "sys",
    "urlencode",
    "uuid",
]
