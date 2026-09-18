# Demo script — 3 minutes

Rehearse once out loud. The timings are real; the pauses are load-bearing.

---

### 0:00 — The problem, as a picture (30s)

Put the two chips side by side on one slide, captions hidden.

> "Two satellite images. One of these is unusable. Point at it."

*(let them look — they always get it right, it's obvious)*

> "Easy, right? Now here's what the catalogue says about them."

Reveal: **cloudy one → 12.2% cloud. Clear one → 47.4% cloud.**

> "Backwards. Every cloud filter in every pipeline in this room uses that number."

### 0:30 — Why it's backwards (20s)

> "Because that number describes a 110-kilometre scene. My field is five kilometres.
> A cloud 80 km away counts against me, and the cloud directly over my field doesn't
> count enough."

### 0:50 — The old way (30s)

Scroll `demo/the_old_way.py` slowly. Don't explain it. Let it scroll.

> "This is what you write to get *one* image. Eighty lines. It has two bugs in it right
> now — that one, and that one. Neither one crashes. Both give you an answer that looks
> completely reasonable."

### 1:20 — The fix (40s)

Type it live. One sentence:

```
NDVI over willamette, August 2024, only clear days
```

> "Here's what it understood." *(parsed spec)*
> "Here's every candidate image — and for each one, what the catalogue claims next to
> what's actually true over my AOI." *(the comparison table — pause here, it's the best
> screenshot in the deck)*
> "It picked the one the filter would have thrown away."

### 2:00 — The part that makes it more than a wrapper (40s)

Scroll to the QA report.

> "And it told me something I didn't ask about. Sentinel-2 changed its reflectance
> encoding in processing baseline 4. If you don't apply a minus-one-thousand offset,
> your NDVI comes out at 0.299 instead of 0.497."
>
> *(beat)*
>
> "Forty percent low. No error. No warning. You'd publish it."

### 2:40 — Close (20s)

> "I work on NASA's smallsat data programme. I've watched people hit all three of these.
> The fix isn't a faster search — it's a search that tells you the truth about what it
> found. One sentence in, analysis-ready data out, and an honest account of what it did."

---

## Q&A — the four you will get

**"Isn't this just pystac-client / stackstac?"**
Those fetch pixels. None of them recompute cloud cover over your AOI, and none warn you
about the offset. The plumbing isn't the contribution — the honesty is.

**"Does it scale / is it production?"**
No, it's a 90-minute demo. It's single-scene, no mosaicking, one collection. The
*concept* scales: AOI-honest cloud scoring is a few seconds per candidate because SCL
is 20 m and you only read your own window.

**"Why not just look at the thumbnail?"**
The thumbnail is of the whole scene too. And you can't put a human eye in a pipeline
that runs nightly over 400 fields.

**"What's the IBM angle?"**
Granite parses the sentence into the query spec, with a deterministic rules fallback so
the demo can't die. *(If you cut the LLM: "the parse is deterministic by design — I
wanted the demo reproducible. Granite drops into that one function.")*

---

## Pre-flight checklist

- [ ] `CHIP_OFFLINE=1` works with wifi physically turned off — test it, don't assume
- [ ] Both hero queries pre-run and cached (a cold COG read on bad wifi is 20+ seconds)
- [ ] Browser zoomed so the comparison table is readable from the back of the room
- [ ] `the_old_way.py` already open in a second window, scrolled to the top
- [ ] The two chips on one slide, as the fallback if everything fails
- [ ] Terminal font size up
