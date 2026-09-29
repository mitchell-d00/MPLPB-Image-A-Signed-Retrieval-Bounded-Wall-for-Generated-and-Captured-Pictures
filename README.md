# MPLPB Image

Document ID: MPLPB-IMAGE-016 v2

A local image wall where every picture is a signed **MPLPB page** that says
what made it. The page is the control object. The file is the artifact.

Paper: `docs/MPLPB_Image.md` · `.txt` · `.pdf`

Image cut of [Networked MPLPB](https://github.com/mitchell-d00/networked-mplpb)
(signatures, origin depth, ratification records), with the reader discipline of
[Smart Local MPLPB](https://github.com/mitchell-d00/SMART-LOCAL-MPLPB-A-Retrieval-Bounded-Front-End-for-a-Local-Web)
and profiles after [Swarm MPLPB](https://github.com/mitchell-d00/mplpb-swarm-scale).

## What it does, plainly

Every picture gets a card with two separate facts: **what the pixels are**
(`generated`, `captured`, or `unknown`) and **where the card came from**
(origin, depth, who signed it).

- Pictures the studio generates are signed at ingest as `generated`.
- Pictures from outside are `generated` only on real evidence: identical bytes
  or a perceptual match to a known generation (resized and re-encoded copies
  included), or the file declaring it (IPTC source type, a C2PA AI assertion,
  a generator named in a software field, diffusion parameters). Captions,
  comments, titles and author fields are free text and are never read as a
  declaration.
- A declaration is only what the file says, so it can be wrong. A named
  person can attest over it with `--override-declared`; the override is
  signed, logged and shown on the page. A match to a known generation can
  never be overridden.
- Nothing becomes `captured` because of what the file says. Camera EXIF is a
  hint anyone can type. A picture is `captured` only when a named person
  attests it, in their own words, and that is signed and logged.
- Everything else is `unknown`, and is served as unknown.
- A learner trains on the attested pictures and scores unknown ones. The score
  is advisory. It never changes a picture's kind, and it reports when it has
  only learned the file format.

Every record, log event, manifest and model is Ed25519-signed by a key that
lives on your machine, never in the seed. `validate` re-derives each picture's
evidence from its bytes, checks every signature, and with `--strict` refuses
anything signed by a key you have not chosen to trust.

## Run

```bash
python3 -m mplpb_image init site          # creates the seed and your signing key
export OPENAI_API_KEY="sk-..."            # or paste in the UI
python3 studio/server.py
```

```bash
python3 -m mplpb_image validate site --strict
python3 -m mplpb_image check site some_picture.jpg        # stores nothing
python3 -m mplpb_image import site some_picture.jpg --title "harbour"
python3 -m mplpb_image attest site IMG-… --who "Name" --statement "I took this at …"
python3 -m mplpb_image attest site IMG-… --who "Name" --statement "…" --override-declared
python3 -m mplpb_image ratify site IMG-… --who "Name"      # generated → approved illustration
python3 -m mplpb_image rescan site                         # after upgrading: re-derive evidence, re-sign
python3 -m mplpb_image learn site
python3 -m mplpb_image profile check site IMG-… --name external
python3 -m mplpb_image key show
python3 -m unittest discover -s tests
```

Environment: `MPLPB_IMAGE_HOME` (signing key and trust store, default
`~/.mplpb-image`), `MPLPB_IMAGE_SITE` (seed, default `site/`),
`MPLPB_IMAGE_PROFILE` (default `studio`), `ART_STUDIO_HOST`/`ART_STUDIO_PORT`
(default `127.0.0.1:8765`), `OPENAI_BASE_URL`.

Standard library only, Python 3.9+. Pillow is optional: with it, JPEG and WebP
get perceptual hashes and content features; without it, only PNG does.

## Upgrading from v1

v1 read generator names out of free text, so a photo captioned "Mi imagen
del puerto" or "the OpenAI office" was stored as declared-generated. After
upgrading, `validate` reports those pages under I.11. Run `rescan` once: it
re-derives each page's evidence, re-signs what changed, and moves pages that
were generated only by such a caption (and anything generated only by
matching them) back to `unknown`, where they can be attested normally.

## What it is not

Not a deepfake detector, not a C2PA validator, not proof. It cannot tell you a
picture is real. It can tell you what evidence exists, who vouched for what,
and whether anyone changed the record afterwards.

## Licence

MIT code. CC BY 4.0 intended for the paper. `mplpb_image/ed25519.py` is carried
unchanged from Networked MPLPB (same author, MIT).

Mitchell D. McPhetridge · September 2026
