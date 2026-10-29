-- Minimal tilemaker profile for the geocoder's offline basemap.
-- Layers: water, landuse, roads, boundaries, places. The dark UI background
-- stands in for the sea, so we draw only land features (no coastline polygons).

-- tilemaker only processes nodes whose tags include one of these keys.
node_keys = { "place" }

function node_function()
  local place = Find("place")
  if place ~= "" then
    Layer("places", false)
    Attribute("kind", place)
    Attribute("name", Find("name"))
    if place == "city" then MinZoom(4)
    elseif place == "town" then MinZoom(7)
    else MinZoom(10) end
  end
end

function way_function()
  local highway  = Find("highway")
  local waterway = Find("waterway")
  local natural  = Find("natural")
  local landuse  = Find("landuse")
  local boundary = Find("boundary")

  if highway ~= "" then
    Layer("roads", false)
    Attribute("kind", highway)
  end

  if waterway == "river" or waterway == "canal" or waterway == "stream" then
    Layer("water", false)
  end
  if natural == "water" or Find("water") ~= "" then
    Layer("water", true)
  end

  if landuse ~= "" then
    Layer("landuse", true)
    Attribute("kind", landuse)
  end

  if boundary == "administrative" then
    Layer("boundaries", false)
    Attribute("admin_level", Find("admin_level"))
  end
end
