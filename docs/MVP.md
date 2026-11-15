# Geometry and texture MVP

The RIF operator now reads game textures directly from loose `graphics`
files or indexed `fastfile` RFFL members. Set **Game Data** to their parent
directory, or leave it blank to discover that directory above the selected
RIF. Import does not invoke the old extractor or create PNG files.

Images are decoded into bounded memory, uploaded to Blender and packed.
UV normalization uses decoded image dimensions. Transparency compares
palette RGB values, and material alpha is connected. Unsupported non-RFFL
files are reported and ignored as in the game; malformed RFFL/ILBM content
raises a named import error.

Level geometry uses the game's 15-bit texture number and signed upper-half
UV index, five-slot polygon records, count-driven UV lists (including empty
lists), and split bitmap count/version headers. Environment scale is the
RIF's ENVSDSCL double times GlobalScale (1 at the game baseline), followed
by the user's Blender unit scale.

The default **Geometry and Textures Only** mode validates only level
containers and known geometry/bitmap fields; unknown leaf data is opaque.
Malformed roots, lists, Huffman tables and shape links are named errors.
Failed imports remove their new datablocks without touching earlier imports.
Input and decoded RIFs each have a 32 MiB cap. Huffman output must complete,
and its bounded code histogram must fill exactly the 11-bit code space.

The release gate covers complete stock imports, placement checks and
malformed-input/rollback cases. Detailed test records are internal.
