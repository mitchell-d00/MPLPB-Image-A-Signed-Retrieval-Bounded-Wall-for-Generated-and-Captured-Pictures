# MPLPB Image

A Signed, Retrieval-Bounded Wall for Generated and Captured Pictures

Mitchell D. McPhetridge
Independent Researcher

------------------------------------------------------------------------------

  Field        Value
  -----------  ----------------------------------------------
  Document ID  MPLPB-IMAGE-016
  Category     Specification / Reference Implementation
  Updated      2026-09-28T00:00Z v2
  Status       current
  Scope        The image cut of an MPLPB local web: how a
               studio and an importer write signed pages
               instead of anonymous files; how a picture's
               kind (generated, captured, unknown) is kept
               apart from its provenance; how evidence,
               perceptual matching, attestation, and a
               bounded learner decide what a reader may treat
               as a photograph
  When to use  Keeping generated and found pictures where a
               later reader, person or model, must not treat a
               generated picture as a photograph; deciding
               what an image folder is allowed to claim

Related. Networked MPLPB (github.com/mitchell-d00/networked-mplpb): signed
descriptions, origin depth, recorded ratification, keys outside corpora.
MPLPB at Swarm and Enterprise Scale (MPLPB-SWARM-013 v3): epistemic
laundering, deployment profiles, the ablation discipline. Smart Local MPLPB
(MPLPB-SMART-011 v1): the retrieval-bounded reader. MPLPB and the Cost of
Visibility (MPLPB-COST-012 v2). The Local Mirror (MPLPB-LOCAL-008 v4).
Continuity Without Memory (2026).

Back to. Main Index > Specification / Architecture Sub-Index

------------------------------------------------------------------------------

## Abstract

A folder of pictures remembers pixels and forgets how they were made. A
later session sees a PNG and a timestamp. It cannot see that a model made
it, from which prompt, or whether anybody ever said it was real. Monday
generated. Thursday treats the file as a found object. That is the
terminal-fact failure from Continuation Must Preserve Revisability,
applied to pixels.

MPLPB Image keeps every picture as a page. Each page records two things
that folders merge and this design keeps apart: what the pixels are
(kind: generated, captured, or unknown) and where the record came from
(origin, origin depth, and a signature). The studio writes generated
pictures and signs them at ingest. An importer brings outside pictures in
and assigns kind from evidence only: identical bytes or a perceptual match
to a known generation, or the file declaring its own generation.

Trust is deliberately asymmetric. A claim that a picture is generated is
accepted from evidence, because the error it risks is caution. A claim that
a picture is captured is never accepted from the file. Camera metadata is a
hint anyone can type. A picture becomes captured only when a named person
attests it in their own words, signed and logged.

A small learner trains on attested pictures and scores unknown ones. Its
score is advisory. It never changes a picture's kind, it never trains on
its own output, and it reports by feature group so it cannot hide that it
has only learned the file format.

Every record, every log event, the manifest fingerprint and the model are
Ed25519-signed by a key held on the machine and never inside the seed.
Twelve checks re-derive what can be re-derived: evidence from bytes,
hashes from files, signatures from keys. A validity check needs only the
seed. A trust check needs the machine's own choice of keys, and the
external profile requires it.

This paper specifies the two axes, the evidence rules, perceptual
matching, signatures and trust, the learner, the checks, the profiles, the
write-backs, and the tests that have and have not been run. It does
not claim to detect generated pictures. It claims to keep track of what is
known about them, and to refuse to invent the rest.

------------------------------------------------------------------------------

## 1. The gap this fills

A generator toy (stdlib HTTP proxy, one wall, key in memory, bytes saved as
received) answers the request question well and the reader question not at
all. Three defects belong to whatever reads the folder later:

- retrieval without provenance — a PNG with no document identity
- model bypass — a later agent captions the file from its own weights
- local/public confusion — a generated file cited as if it were a photograph

A fourth appears as soon as the folder takes in pictures from outside:

- kind confusion — nobody can say which pictures were made and which were taken, and the absence of a generator tag is read as a camera

No amount of request quality in the studio prevents any of these. MPLPB
Image is the reader discipline for the folder.

## 2. What a front end is allowed to do

The asymmetry is Smart Local §2. A wall that says I don't have that picture
is mildly annoying. A wall that invents a caption indistinguishable from a
stored record is corrosive, because after a few undetected instances the
record on every other picture stops meaning anything.

ask() therefore has no path that describes a picture without a catalog
hit. When nothing matches it lists the scopes that exist and stops. When
something matches it returns the stored prompt or import title, labelled as
such, with the picture's kind, origin, depth, status, and a citation line.
It never returns a description of a scene. Matching is on whole words.

## 3. Two axes

Folders collapse two questions into one file. This design keeps them apart.

Kind answers: what are these pixels?

  kind        how a picture gets it
  ----------  --------------------------------------------------------
  generated   signed ingest by this studio; identical bytes to, or a
              perceptual match of, a known generation; or the file
              declares generation (IPTC, C2PA AI assertion, generator
              tag, diffusion parameters)
  captured    a named person attests it; or it is a byte-identical
              copy of a picture someone attested
  unknown     everything else

Origin answers: who stands behind this record, and how far is it from a
person?

  origin      depth   meaning
  ----------  ------  -----------------------------------------------
  machine     >= 1    generated; nobody has reviewed it
  ratified    0       generated; a named person approved it as an
                      illustration
  human       0       captured; a named person attested it
  unknown     >= 1    brought in from outside with no attestation

The combinations are closed. Generated is machine or ratified. Captured is
human at depth 0. Unknown is never depth 0. Ratification never changes
kind: an approved illustration is still generated and is never served as a
photograph. Networked MPLPB's origins (human, machine, ratified) carry over
unchanged; unknown is added because an importer, unlike an author, often
does not know.

### 3.1 Asymmetric trust

Evidence of generation is accepted when it is strong. The cost of wrongly
calling a photograph generated is that a real picture is served with a
caution label. That is the cheap error.

Evidence of capture is never accepted from the file. EXIF make, model,
exposure and GPS can be written by anyone with a text editor, and the
absence of a generator tag means only that the tag is absent. A picture is
captured only by attestation or by being byte-identical to an attested
picture. The cost of wrongly calling a generated picture captured is the
failure this whole design exists to prevent, so nothing a file says about
itself can cause it.

The cheap error is cheap only if it can be corrected. When a picture is
generated because of what the file declares and for no other reason, a
named person may attest it as a capture over that declaration, but only by
asking for it explicitly (§12). The overridden signals stay in the signed
record, in the signed log and on the page. A match to a known generation is
evidence about the pixels, not a claim the file makes, and is never
overridable.

## 4. Layers

  Layer          Modules                     Owns
  -------------  --------------------------  ------------------------------
  1. Seed        corpus.py, validate.py      what a picture page is; whether
                                             the wall is well-formed
  2. Evidence    evidence.py, pixels.py      what the bytes say about
                                             themselves; perceptual hash;
                                             content statistics
  3. Kind        classify.py                 generated / captured / unknown,
                                             from evidence only
  4. Provenance  keys.py, ed25519.py         signatures, signer registry,
                                             local trust store
  5. Learner     learn.py                    advisory scores from attested
                                             labels
  6. Retrieval   retrieve.py, cite.py        finding pages; citations that
                                             carry kind and signer
  7. Decision    profiles.py                 how a deployment may serve a
                                             picture
  8. Write-back  lifecycle.py, studio        ingest, import, retire, ratify,
                                             attest

The seed never holds a secret key and never holds the API key. The studio
loads the signing key from the machine at start and never serves it.

## 5. Ingest and import

Ingest (the studio, on a 200 from /v1/images/generations):

1. Decode b64_json, or fetch url over https only, capped at 64 MB.
2. Refuse anything whose bytes are not PNG, JPEG, or WebP by signature.
3. Read evidence and compute the perceptual hash, without changing a byte.
4. Allocate IMG-YYYYMMDD-XXXXXX, unique within the catalog.
5. Write the bytes unchanged under wall/, named by what they are.
6. Write the record: kind=generated, basis=signed-ingest, origin=machine,
   depth 1, prompt and prompt hash, model, evidence summary, dHash.
7. Sign the record, append a signed hash-chained lineage event, re-render
   the wall and log, rebuild and sign the manifest.

Import (the CLI, for a picture from anywhere):

1. The same byte checks, evidence and hash.
2. Refuse bytes already on the wall as a current page.
3. Decide kind by §3 from the catalog and the evidence.
4. A generated import is machine at depth >= 1, inheriting the depth of
   what it matched; copying never resets depth. An unknown import is origin
   unknown at depth 1. A captured import (a byte-identical copy of an
   attested picture) carries the attestation over.
5. If a learner exists, attach its advisory score to an unknown import.
6. Sign, log, render, manifest, as for ingest.

check does steps 1 and 3 and stores nothing.

## 6. Evidence

evidence.py reads container metadata from PNG (IHDR, tEXt, zTXt, iTXt,
eXIf, iCCP, caBX), JPEG (APP1 EXIF and XMP, APP2 ICC, APP11 JUMBF,
quantization and frame headers) and WebP (VP8X, VP8L, VP8, EXIF, XMP,
ICCP, C2PA). It never decodes pixels and never modifies bytes. Every read
is bounded, and a truncated or malformed file produces parse errors, not
exceptions; a fuzz test mutates files and requires the parser to survive.

Each signal is sorted by what it points to and how strong it is.

  signal                                    points to   strength
  ----------------------------------------  ----------  ---------
  IPTC digital source type: trained or      generated   declared
    composite algorithmic media
  C2PA manifest asserting AI generation     generated   declared
  a known generator named in EXIF           generated   declared
    software, make or model, XMP creator
    tool, C2PA claim generator, or a PNG
    text key generators write (Software,
    Source, Generator, parameters, prompt,
    workflow)
  diffusion parameters or a node workflow   generated   declared
    embedded in PNG text
  IPTC digital source type: digital         captured    hint
    capture, film, print
  camera make or model; exposure; GPS       captured    hint
  C2PA manifest without an AI assertion     captured    hint
  image-API standard size, no camera data   generated   hint

Declared generation sets kind. Hints set nothing; they are shown on the
page and given to the learner.

Generator names are looked for only in fields whose job is to name the
software that wrote the file. Captions, descriptions, titles, comments and
author fields are free text written by people, and are never searched. v1
searched them, and a photograph captioned "Mi imagen del puerto", "the
OpenAI office" or "luminous flux at dusk" was recorded as declaring its own
generation. Patterns for names that are also ordinary words (Imagen, FLUX)
require a version or vendor form. A JUMBF box counts as C2PA only when its
label says c2pa.

C2PA manifests are detected and not validated. This implementation does not
check their signatures, and every C2PA field it reports says verified:
false. A signed C2PA manifest from a trusted issuer is stronger evidence
than anything here; validating one needs a certificate chain this design
does not carry.

The evidence summary is stored in the signed record and re-derived from
the bytes at every validation (I.11), so a record cannot claim a camera the
file does not name, even when the key holder signs the claim.

## 7. Perceptual matching

Networked MPLPB states that verbatim copying does not reset depth. Without
matching, that rule could only be contained: a copied PNG kept its record
only while it kept its card. A picture resized for a message or re-encoded
by a platform arrived as a stranger.

pixels.py computes a 64-bit difference hash over a 128-pixel thumbnail.
Import compares it with every picture in the catalog, current and retired.
A distance of 8 bits or less counts as the same picture. A resized,
re-encoded copy of a studio generation is therefore imported as generated,
with basis perceptual-match, naming the page it matched and inheriting its
depth. For resizing and recompression, FM-N7 is detected, not only
contained.

Three limits are stated because each was found in testing:

- Near-flat pictures collide. A clear sky and a generated gradient hash alike. A hash with fewer than 8 or more than 56 set bits is not used for matching; exact byte matches still count.
- A perceptual match to an attested capture does not make an import captured. Only byte identity carries an attestation over. An edited copy of a photograph is unknown, with the resemblance reported as a conflict.
- Cropping, flipping, heavy editing, and a deliberate adversary defeat a dHash. A match means very probably the same picture. No match means nothing.

PNG is decoded with the standard library under two hard limits: a
40-megapixel cap read from the header, and a decompressed-size cap enforced
while inflating, so a small file that inflates to gigabytes is refused.
Pillow, if installed, decodes JPEG and WebP. Both paths share one exact
area-average downsampler, so a PNG hashes identically with or without
Pillow and a seed validates the same on either machine. An earlier draft
used Pillow's own resize and disagreed with the standard-library path by
six bits on a noisy picture; a test caught it.

## 8. Signatures and trust

Each machine holds an Ed25519 key under MPLPB_IMAGE_HOME, created on first
use with mode 0600, outside every seed. The node ID is N- and the first
sixteen hex digits of the SHA-256 of the public key, as in Networked MPLPB,
and the signature code is Networked MPLPB's pure-Python RFC 8032
implementation carried unchanged.

What is signed: every record (over its identity, paths, hashes, kind,
basis, origin, depth, evidence, and the names and times of any
ratification or attestation); every lineage event, each naming the hash of
the one before; the manifest fingerprint; and the learner model. The seed
holds only public keys, in _net/signers.json.

Validity and trust are different questions and are answered separately.

  question  asks                                   answered by
  --------  -------------------------------------  -------------------
  valid     does each signature verify against     the seed alone
            the key the seed names?
  trusted   is that key one this machine chose     the local trust
            to trust?                              store only

A write that finds signers.json naming a different key for its own node ID
refuses rather than repairing it. An earlier draft of this implementation
silently rewrote the entry and erased the evidence; a test caught it.

### 8.1 Threat model

  attacker                          outcome
  --------------------------------  ----------------------------------------
  edits a record, page, log entry,  signature or chain fails (I.9); page and
  or file without a key             catalog disagree (I.3); manifest (I.8)
  relabels a generation as a        signature fails; no attest event (I.5);
  capture without a key             external refuses
  holds a key and signs a false     evidence re-derived from bytes (I.11)
  camera into the evidence          disagrees
  writes a secret key into a seed   I.10 fails
  re-signs everything with their    valid; untrusted; validate --strict and
  own key                           the external profile refuse
  swaps the public key for a node   the ID no longer hashes from the key
  ID                                (I.9); writes refuse
  holds a trusted key and attests   succeeds. Attestation is a signature,
  a generated picture that carries  not a proof; the attester's name is on
  no generation evidence            the record and in the log
  strips metadata and crops a       arrives as unknown. Nothing here can
  generated picture                 tell, and nothing here claims to

The last two rows are the limits. A trusted key that lies is outside what
signatures can fix; the design makes the lie attributable and permanent in
the log. A laundered picture with its evidence removed is outside what
evidence can fix; the design keeps it unknown rather than guessing.

## 9. The learner

The request behind this section is to learn the difference between real
and generated. A learner that writes its answer onto a card is a truth
engine, and a laundering channel: a guess becomes a document, and the next
reader retrieves it as a finding. Swarm MPLPB §5 is the same mechanism with
text. So the learner is bounded by three rules.

Labels come only from attested pages: pictures the studio signed at ingest
(generated) and pictures a named person attested (captured). Declared
metadata, perceptual matches, and the learner's own earlier scores are
never labels. A model trained on its own guesses grades itself.

Features are recomputed from the bytes at training time, after the bytes
are checked against their signed hash. A picture whose bytes changed is
skipped and named, not learned.

The output is advisory. It is a probability with the model hash beside it.
It never changes a picture's kind, origin or depth, and an attested capture
drops any score it had.

The model is a logistic regression in the standard library, over three
feature groups:

  group            features
  ---------------  ---------------------------------------------------
  format           container, API-standard size, squareness, pixel count
  metadata         EXIF presence, camera, exposure, GPS, original date,
                   software, ICC, XMP, PNG text, JPEG quality, progressive
                   scan, chroma subsampling
  content          compressed bytes per pixel, luminance spread,
                   Laplacian mean and variation, Laplacian-to-gradient
                   ratio, clipping, saturation, colour diversity

Declared generation signals are excluded. The rules in §3 already act on
them, and including them lets a model score well by re-learning a rule.

Cross-validation is stratified and reported three times: all features,
format and metadata only, content only. If format and metadata alone score
within three points of all features, the report warns that the model is
reading file type, size and EXIF, which stripping or re-encoding defeats.
That warning is the point of the ablation. The first synthetic test set
separated perfectly on format features because noisy pictures compress
worse; compressibility was moved to the content group, where it belongs.

The model file is signed. I.12 checks that every training entry names an
attested page whose bytes still match.

What the learner has shown: on synthetic pictures, it separates smooth
renders from noisy textures on content alone and warns when a set can be
separated by format alone. What it has not shown: anything about real
generators against real cameras. §16 states what that needs.

## 10. Checks

  check  asks
  -----  ------------------------------------------------------------------
  I.1    index.html exists and carries a document ID
  I.2    the wall lists every current picture and no retired one
  I.3    every catalog entry has its page, and the page agrees with the
         catalog on ID, status, kind, basis, origin, depth, and signature
  I.4    document IDs are well-formed and unique
  I.5    kind, basis, origin and depth form an allowed combination (§3);
         ratification and attestation have matching lineage records; a
         capture whose file declares generation carries an override that
         the log records identically;
         matches name real pages of the right kind; no picture page sits
         outside the catalog
  I.6    the bytes each page names exist and match their signed hash; no
         image anywhere in the tree lacks a page
  I.7    every link resolves to a file inside the root
  I.8    the manifest lists exactly the tree with current hashes, and the
         fingerprint matches it
  I.9    every record, event, fingerprint and model is signed and verifies
         against the key the seed names; the lineage chain is unbroken;
         with --strict, every signer is trusted here
  I.10   no secret key material anywhere in the seed
  I.11   stored evidence and perceptual hash are what the bytes yield
  I.12   the learner was trained only on attested pages whose bytes match

I.1 to I.8 say the tree is what the catalog says. I.9 to I.12 say the
catalog is what its signers said, and what the bytes say.

## 11. Profiles

A picture can be served four ways, and only the first two count as source.

  serve as               requires
  ---------------------  ----------------------------------------------
  photograph             captured, human, attested, current
  approved illustration  generated, ratified, current
  generated, labelled    generated, unratified; shown, never source
  unverified image       unknown; shown only where the profile allows

                         studio   lab   internal   external
  max served depth            4     2          1          0
  generate                  yes   yes        yes         no
  ratify                     no   yes        yes         no
  attest                     no   yes        yes         no
  show unknown              yes   yes        yes         no
  trusted signer needed      no    no        yes        yes

No profile serves a generated picture as a photograph. No profile serves
an unratified generation or an unknown picture as source. A record whose
signature does not verify is not shown at all. Internal and external serve
nothing as source unless its signature verifies against a key this machine
trusts, and a check that did not look at trust counts as a failure.

The studio reads MPLPB_IMAGE_PROFILE and will not generate under external.
Ratify and attest name the profile they run under and refuse under studio
and external.

## 12. Write-backs

Retire moves a current page and its bytes, unchanged, to _log/superseded/,
off the wall, and may name a current successor.

Ratify applies to a current, unratified generated picture. It names who,
stamps when, sets origin ratified at depth 0, and leaves kind generated.

Attest applies to a current unknown picture. It names who, stamps when, and
requires a statement in the attester's own words of what the picture is
and how they know. It sets kind captured, origin human, depth 0. It refuses
any picture that matches a known generation, exactly or perceptually: the
evidence wins. It refuses a picture generated only by declared metadata
unless the attester asks to override (--override-declared). An override
copies the declared signals into the signed record and the attest event,
adds a conflict to the page, and is checked by I.5. A byte-identical copy
of such a capture inherits it; no other picture does.

Rescan re-derives every page's stored evidence from its bytes with the
current reader and re-signs what changed. A generated page whose only
basis was a declaration the reader no longer finds becomes unknown, and so
does any page generated only by matching it. It is how a seed written by an
earlier reader is brought forward; until it is run, I.11 reports the
difference.

All four are signed, logged with the same who and when as the record, and
checked against each other by I.5.

## 13. Teaching analogue

Smart Local turns I don't know into a taught page. Image MPLPB does not. A
session may not type this is a photo of X onto a file and receive a
document ID that makes the claim look considered. The only route to
captured is a person's name and their own sentence, signed; and the only
thing a model may write onto a card is a score that says it is a score.

## 14. Cost

MPLPB-COST-012 spends structure at authoring time so retrieval stays
ordinary. Here the authoring event is the generation or the import. C_s is
one page, one signature, one evidence read and one hash per picture. C_i is
a JSON catalog, a lineage log and a manifest. Validation re-reads every
picture, so its cost grows with the wall; that is the price of I.11. C_r
for a later agent is read the card instead of inferring origin from pixels.

Attestation is human attention, and Swarm MPLPB §9.2 applies unchanged: it
does not amortize. A wall that needs many captures attested needs many
people, and a population that attests without looking has converted the
check into a formality (FM-S9).

## 15. What this does not claim

It does not detect generated pictures. It records evidence and refuses to
invent more. It cannot tell a real photograph from a generated picture with
its metadata stripped; it keeps such a picture unknown. It does not validate
C2PA signatures. A signature proves which key signed a record, not that the
signer told the truth. Origin depth is not a reliability score, and
structural validity is not aesthetic quality or prompt fidelity. The
learner's numbers here come from synthetic pictures and transfer to
nothing. The pure-Python Ed25519 is correct against the RFC vectors and not
constant-time. Structure has not been shown to beat a timestamped outputs/
folder.

## 16. Falsifiers

F1. A stranger given only site/ cannot tell generated, captured, unknown,
    and retired pictures apart.
F2. A copy that validates here fails on another machine.
F3. ask() returns a caption for a query that hits no page.
F4. A generated picture reaches captured, or depth 0 without a
    ratification record, and still validates.
F5. Storing a picture changes its bytes.
F6. Any profile serves a generated picture as a photograph, or an
    unratified or unknown picture as source.
F7. Flat retrieval over filenames matches ask() on correct refusals.
F8. A resized or re-encoded copy of a studio generation imports as unknown
    at a material rate.
F9. A record edited without the signing key validates.
F10. The learner's advisory score changes a picture's kind, or a
    non-attested page becomes a training label.
F11. On real pictures, the learner's content-only accuracy is near chance,
    or its format-only accuracy explains all of its result.
F12. Free text a person wrote into a file (a caption, comment, title or
    author) makes the picture declared generated, or a declared-only
    generation cannot be corrected by a logged, named attestation.

The reference suite is 93 standard-library unittest cases, including a
fuzz pass over the evidence parser and an end-to-end run of the studio
against a local fake Images API. It bears on F2 (a copied seed validates at
a new path, and PNG hashes agree with and without Pillow), F3, F4
(relabelling with and without a key, forged ratification, forged
attestation, forged evidence), F5, F6, F8 (half-size re-encoded copies of
synthetic pictures), F9 (catalog, lineage, fingerprint, signer registry and
learner tampering), F10, and F12 (captions in five fields, a JPEG comment,
the override and its removal, and a rescan of a seed written by the v1
reader). It also checks the Ed25519 code against RFC 8032 §7.1 vectors 1
to 3. It bears on the structural half of F1. It does
not bear on F7 or F11.

F11 needs a real set: a few hundred generations from several current
models and a few hundred photographs from several cameras, attested by
their owners, with the same pictures also stripped of metadata and
re-encoded, and a probe author who did not choose the training pictures.
F8 needs the same real set run through common platforms' resizing. Until
those are run, the learner is a mechanism, not a result.

## 17. Commands

    python3 -m mplpb_image init site
    python3 studio/server.py
    python3 -m mplpb_image validate site [--strict]
    python3 -m mplpb_image check site picture.jpg
    python3 -m mplpb_image import site picture.jpg --title "…"
    python3 -m mplpb_image attest site IMG-… --who "Name" --statement "…" [--override-declared]
    python3 -m mplpb_image rescan site
    python3 -m mplpb_image ratify site IMG-… --who "Name" [--profile lab]
    python3 -m mplpb_image retire site IMG-… --reason "…" [--successor IMG-…]
    python3 -m mplpb_image learn site
    python3 -m mplpb_image ask site "harbour"
    python3 -m mplpb_image cite site IMG-…
    python3 -m mplpb_image profile check site IMG-… --name external
    python3 -m mplpb_image key show | list | trust PUBLIC_KEY --name "…"
    python3 -m unittest discover -s tests

Exit codes: validate 1 on any failure; ask 4 on not in this corpus; profile
check 3 when the picture would not be served as source.

## 18. Changes in v2

v1 searched free text for generator names, so a caption could label a
photograph as declaring its own generation, and attest refused every
generated picture, so that label could not be removed. v2 reads generator
names only from software fields (§6), lets a named person override a
declaration-only generation on explicit request (§3.1, §12), adds rescan
to bring older seeds forward, and adds F12. Records written by v1 keep their
signatures; the override field is signed only where it is present. A v1
seed whose pictures carry captions will fail I.11 under v2 until rescan is
run, which is the check doing its job.

## 19. Conclusion

The toy hangs pictures. The seed remembers which were made and which were
taken, and refuses to decide the second from anything a file says about
itself. A copy that has been resized comes home to its card. A record that
has been edited without the key stops validating. A key nobody trusts
still signs validly and is still refused where it matters. The learner
learns, says what it learned from, and is not allowed to write.

What it cannot do is tell a real photograph from a clean generated picture
with nothing attached. It says unknown. That is the honest answer, and the
design is arranged so that the honest answer is the one that survives.

Mitchell D. McPhetridge
September 2026
