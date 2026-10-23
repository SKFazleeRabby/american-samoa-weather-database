# Geographic data attribution

American Samoa ADM0 coastline geometry is from geoBoundaries gbOpen,
boundary ID `ASM-ADM0-72969383`, representing 2021, built December 12, 2023.

- Catalog: https://www.geoboundaries.org/api/current/gbOpen/ASM/ADM0/
- Source: https://github.com/wmgeolab/geoBoundaries/tree/9469f09/releaseData/gbOpen/ASM/ADM0
- License: Creative Commons Attribution 4.0, https://creativecommons.org/licenses/by/4.0/
- Original boundary source identified by geoBoundaries: Sentinel-2 10-Meter Land Use/LandCover.

The geometry is bundled as a JavaScript data object in `dist/coastline.js`.
Display colors, outlines, and labels are customized. Labels are manually placed
for readability. These are geographic boundaries for visual exploration, not
survey or navigation data. Station locations are read from the PostgreSQL station
metadata; the production application does not include the prototype's demo stations.
