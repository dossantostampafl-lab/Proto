# Floor Control — visual fidelity acceptance contract

Status: implementation specification, NOT proof of visual completion.

## Reference
Five user-supplied screenshots in the conversation (24 October 2026) show a high-angle isometric futuristic operations room. They are visual references, not repository assets. Do not redistribute screenshots or copy external characters/branding.

## Geometry and art direction
- The holographic blue Earth is the primary focal point, centered inside a substantial circular multi-console hub. It must remain large and recognizable on mobile.
- Several tiers of densely populated operator consoles wrap the hub, especially on left and rear walls; visible seats, monitors, railing, stairs, plants and walkways give architectural depth.
- Metallic blue/graphite surfaces, luminous cyan rings, restrained warm amber accents, reflective floor, physically coherent perspective and contact shadows.
- Avoid a sparse room, oversized empty floor, tiny globe, flat schematic or generic stock office.
- The full desktop scene should read as one coherent elevated-camera composition; portrait must be independently framed, not a blind desktop crop.

## Existing implementation to preserve
- Blender source: tools/render_control_room/render.py
- Output images: apps/web/public/floorcontrol/room.webp and room-portrait.webp
- Anchors: apps/web/src/views/floorControl/anchors.json
- Live overlays: apps/web/src/views/floorControl/FloorControl.tsx
- Keep source-generated Blender geometry and original PROTO sprites. Do not embed reference screenshots as background.
- Keep API-backed orders, signals, agents, ledger, queue and source freshness. Missing data remains unavailable, never fictional.
- Preserve opt-in ChatGPT integration and financial safety controls; no order authority added.

## Acceptance and validation
1. Render desktop and mobile using the existing Blender pipeline and regenerate anchor coordinates together.
2. Capture the running page at 1440x900 and 390x844 using actual API data or clearly labeled simulation.
3. Compare screenshots side-by-side against all five supplied references. Check hub scale, desk density, lighting, floor occupancy, perspective and overlays.
4. Ensure all agent labels are legible and do not obscure the central globe; all agents remain discoverable through the existing UI.
5. Verify responsive layout, no horizontal scrolling, no console errors, correct anchor alignment, reduced-motion behavior.
6. Run frontend typecheck, contract-check, build, bundle budget and Playwright browser checks. If SUMMARY LIVE assertions fail, inspect the live UI contract before changing selectors.
7. Include before/after screenshots and test evidence in the implementation PR. Do not claim visual parity based on code checks alone.

This document authorizes no deployment or merge by itself.

## Frozen approved 16:9 mockup (25 October 2026)
The user approved the subsequent PROTO preview image as-is and requested full implementation without further stylistic changes. This supersedes interpretation choices in earlier drafts. Maintain its spatial hierarchy: compact dark top status line; top-left large total card; top-center today's activity card; top-right sources card; enormous central cyan Earth enclosed by a glowing circular command hub; operator desks on left/right/rear tiers; the four floating agent markers; left-bottom agent list; right-bottom live activity list; full-width event queue footer.

The reference image contains **illustrative, non-PROTO recruiting/application metrics**. Those values, platform names, agent names and activity strings MUST NOT be baked into the application or artwork. Preserve their **visual arrangement only**, mapping to existing PROTO orders/agents/venues/journal feeds. The mockup is a visual design approval, not authorization to report fake trades, market execution, or agent activity.

Implementation source-of-truth: Blender scene geometry and exported anchors for background/sprite positions; React/CSS for all data and UI. A successful build alone does not prove equivalence to the approved preview. Verify against an actual browser screenshot before changing PR from draft to ready.
