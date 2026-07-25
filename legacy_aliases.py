"""Backward-compatible node class aliases for saved ComfyUI workflows.

v4.0.0 removed ``DigitReplicateSeedance`` from ``NODE_CLASS_MAPPINGS``, which
broke workflows saved while the alias was still registered. v4.0.1 restores it
as a thin subclass of ``DigitDanceVideo`` (see ``seedance_video_node.py``).

Rename map (3.x / pre-4.0.0 → current)
---------------------------------------
``DigitReplicateSeedance`` → ``DigitDanceVideo`` (provider=replicate)

No other DIGIT node class ids were renamed between the last 3.x line and v4.0.0.
New nodes added in 3.x–4.x (``DigitDanceVideo``, ``DigitOmniVideo``,
``DigitGptImage``, ``DigitSeedreamImage``, ``DigitMuSeedanceCharacter``, etc.)
use new ids and do not shadow older names.

Deprecation policy (DIGIT-168)
--------------------------------
* On major version bumps, old class ids stay registered as aliases for at least
  one full major cycle (e.g. v3 aliases kept through v4, removed no earlier
  than v5).
* Aliases keep the original widget surface so saved workflow JSON loads
  without manual edits; they forward to the replacement node at runtime.
* Release notes list every rename or removal. Deprecated aliases are tagged
  ``[deprecated]`` in ``NODE_DISPLAY_NAME_MAPPINGS``.
* Before removing an alias, grep fleet workflow exports and notify artists
  with a migration window.

Removal schedule
----------------
``DigitReplicateSeedance``: restored in v4.0.1; scheduled removal in v5.0.0.
"""

# Documented alias → canonical replacement (for grep / migration tooling).
LEGACY_CLASS_ALIASES = {
    "DigitReplicateSeedance": "DigitDanceVideo",
}

# Aliases scheduled for removal in the next major bump.
ALIASES_REMOVAL_MAJOR = 5
