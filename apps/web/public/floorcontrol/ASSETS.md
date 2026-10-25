# Control-room images ("Controle" view)

| File | What | Source |
|---|---|---|
| `room-photo.webp` | The mission-control room picture (1672×941): globe, offices and stations around it | The dashboard mockup supplied and approved by the repository owner |
| `robot.webp` | PROTO's own robot, four walk frames, transparent background | `tools/render_control_room/render.py` (Blender, modelled in code) |

The picture is a mockup and carries sample text and numbers. None of them is shown as data:
`src/views/floorControl/FloorControl.tsx` draws an opaque live panel over every sample panel, tag
and header in the picture, at the picture's own pixel coordinates, and every value on the view
(totals, agents, status, queue, sources, events) comes from the API. When there is no data the
panel says so (UNAVAILABLE, ESTAÇÃO LIVRE); it never falls back to the picture's sample values.
