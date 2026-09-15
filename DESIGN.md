---
name: "AI Space Generator"
description: "Mobile-first, local-first photo masking and space-concept editing interface."
colors:
  background: "#0e1012"
  surface: "#171a1d"
  surface-raised: "#202429"
  line: "#30363d"
  text: "#f3f4f5"
  muted: "#a5adb6"
  accent: "#c8ff72"
  accent-ink: "#17200c"
  danger: "#ffb4a9"
typography:
  headline:
    fontFamily: "Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "clamp(1.45rem, 6vw, 2.25rem)"
    letterSpacing: "-.045em"
  title:
    fontFamily: "Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "1.3rem"
    letterSpacing: "-.025em"
  label:
    fontFamily: "Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: ".72rem"
    fontWeight: 800
    letterSpacing: ".15em"
rounded:
  card: "22px"
  button: "15px"
  field: "14px"
  media: "16px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.button}"
    padding: "10px 14px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.button}"
    padding: "10px 14px"
  card:
    backgroundColor: "rgba(23,26,29,.94)"
    textColor: "{colors.text}"
    rounded: "{rounded.card}"
    padding: "18px"
---

# Design System: AI Space Generator

## Overview

**Creative North Star: "Local-first Editing Console"**

This is a dark, mobile-first utility interface built around a single editing workflow: bring in a space photo, mark an area, choose a transformation, generate a concept, and compare the result. The implementation favors direct manipulation, compact controls, strong state legibility, and privacy-oriented local processing over decorative presentation.

The visual system is neutral and low-saturation by default. A single lime accent carries the strongest action and active-state emphasis, while pale text and muted gray-blue copy preserve hierarchy across dark surfaces. Rounded containers and controls soften an otherwise technical editing console; a sticky translucent top bar and restrained shadows provide depth without changing the dense, task-first character.

Evidence status: the quantitative rules below are extracted from the current root PWA implementation. Descriptive language is evidence-derived for this bounded test and is not a separate brand-authority decision.

**Key Characteristics:**
- Dark neutral layered surfaces with one high-salience lime accent.
- Mobile-first single-column flow that expands controls at 720px.
- Rounded cards and controls with compact, touch-oriented sizing.
- Persistent emphasis on local processing, explicit status, and before/after comparison.

## Colors

The incumbent palette uses one accent against layered near-black surfaces, with cool neutral text and a soft coral danger state.

### Primary
- **Lime Action Accent** (`accent`): Primary actions, upload affordance, active tool state, eyebrow labels, and range accent.
- **Deep Accent Ink** (`accent-ink`): Text placed on lime action surfaces.

### Neutral
- **Canvas Background** (`background`): Page background.
- **Base Surface** (`surface`): Core surface family.
- **Raised Surface** (`surface-raised`): Default button surface.
- **Structural Line** (`line`): Borders and separators.
- **Primary Text** (`text`): Main text and strong labels.
- **Muted Text** (`muted`): Supporting copy, metadata, status, and secondary labels.

### Secondary
- **Danger Coral** (`danger`): Error/status text when an operation fails or input is invalid.

**The One Accent Rule.** Lime is the only high-salience interaction color in the current PWA; do not introduce a second competing action accent without a deliberate redesign.

## Typography

**Display Font:** Inter with system sans-serif fallbacks  
**Body Font:** Inter with system sans-serif fallbacks

**Character:** Compact, utilitarian, and legible. Large type is restrained; hierarchy comes from size, weight, tracking, and muted-versus-primary contrast rather than multiple font families.

### Hierarchy
- **Headline** (`clamp(1.45rem, 6vw, 2.25rem)`, `-.045em`): Main application title.
- **Title** (`1.3rem`, `-.025em`): Section headings.
- **Label / Eyebrow** (`.72rem`, `800`, `.15em`): Upper-level section markers such as AI SPACE GENERATOR, COMPARE, and NEXT.
- **Body** (inherited root size): Instructional and explanatory copy.
- **Supporting labels** (`.75rem`–`.9rem` observed): Metadata, controls, footer, notices, and status.

**The Single Family Rule.** The current implementation uses one sans-serif family stack throughout; type hierarchy is created without introducing a second family.

## Layout

The interface is mobile-first. The application shell is centered with a maximum width of `880px`, using compact horizontal padding and vertically stacked cards. Most editing sections remain single-column on mobile.

At `720px` and above, the controls section becomes a two-column grid while upload and metadata span the full width. Repeated `8px` gaps appear in compact tool/action grids, while cards use `18px` internal padding in the incumbent implementation.

The top bar is sticky and accounts for device safe-area insets. The workspace and comparison surfaces scale media to the container width, preserving a direct editing relationship between source, mask, and result.

## Elevation & Depth

Depth is restrained and functional. Cards use a diffuse shadow (`0 20px 50px rgba(0,0,0,.18)`) over dark layered surfaces. The sticky top bar uses a translucent dark background and `backdrop-filter: blur(18px)`. Media wells use deeper near-black backgrounds to visually separate editable image content from controls.

**The Functional Depth Rule.** Elevation distinguishes persistent chrome, cards, and media workspaces; it is not used as decoration on every control.

## Shapes

Cards use the broadest corners (`22px`). Buttons use `15px`, form fields `14px`, and media/comparison wells `16px`. Metadata chips use smaller `12px` corners. The comparison handle is circular, providing a singular geometric contrast inside the otherwise rounded-rectangle system.

## Components

### Buttons
- **Shape:** Rounded rectangle (`15px`) with a minimum touch-oriented height of `46px`.
- **Primary:** Lime accent background with deep accent ink; shared padding is `10px 14px`.
- **Default:** Raised dark surface with structural border.
- **Ghost:** Transparent background while retaining the shared button shape and typography.
- **Disabled:** Reduced opacity (`.42`) and default cursor.

### Tool Buttons
- **Style:** Same base button system with compact inline padding.
- **Active:** Border and text shift to the lime accent.

### Cards / Containers
- **Corner Style:** Broad rounded corners (`22px`).
- **Background:** Translucent dark surface (`rgba(23,26,29,.94)`).
- **Border:** One-pixel structural line.
- **Shadow Strategy:** Single diffuse card shadow.
- **Internal Padding:** `18px` by default; the media workspace card reduces this to `8px`.

### Inputs / Fields
- **Style:** Full-width dark inset field, structural border, and `14px` corners.
- **Select / Color Field Height:** `48px`.
- **Textarea:** Same visual family with internal `12px` padding and vertical resizing.

### Upload Control
- **Style:** Full-width primary-action surface using the lime accent.
- **Behavior:** Visually styled label wrapping a visually hidden file input.

### Empty State
- **Style:** Centered, muted instructional state with stronger primary text for the first action cue.
- **Minimum Height:** `260px`.

### Before / After Comparison
- **Style:** Dark media well with `16px` rounding.
- **Interaction:** Horizontal clipping controlled by an invisible full-surface range input; a white two-pixel divider and circular handle mark the comparison position.

## Do's and Don'ts

### Do:
- **Do** preserve the existing dark neutral hierarchy and use lime for primary/active emphasis.
- **Do** keep root PWA controls touch-oriented, including the incumbent button minimum height.
- **Do** preserve the mobile-first flow and the observed `720px` control-layout breakpoint unless responsive behavior is intentionally redesigned.
- **Do** keep status and privacy/local-processing copy visually legible as part of the operating workflow.

### Don't:
- **Don't** treat the duplicated `ai-space-generator-pages/` files as separate design evidence; they are deployment mirrors of the root PWA files.
- **Don't** infer Gradio's default theme as part of this PWA design system; `ai-space-generator/app.py` is a separate surface.
- **Don't** introduce new palette roles, type families, or component variants as if they were incumbent tokens without implementation evidence.
- **Don't** promote one-off values to reusable tokens solely because the same number appears in unrelated layout contexts.
