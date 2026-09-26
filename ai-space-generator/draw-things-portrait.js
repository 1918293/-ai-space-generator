//@api-1.0

// Hao local portrait workflow for Draw Things.
// Runs on-device after Draw Things downloads the required built-in models.
// The reference photo is selected inside Draw Things and is never stored in this repository.

const MODEL = "realistic_vision_v5.1_q6p_q8p.ckpt";
const FACE_CONTROL = "IP Adapter Plus Face (SD v1.x)";
const FACE_CONTROL_FILE = "ip_adapter_plus_face_sd_v1.x_open_clip_h14_f16.ckpt";

const PROMPT = [
  "photorealistic full-body lifestyle portrait of the same adult woman",
  "preserve the same recognizable face and facial structure",
  "natural skin tone",
  "long burgundy-red straight layered hair with side-swept bangs",
  "individual realistic hair strands and subtle flyaways",
  "wearing the same deep burgundy swimsuit design with gathered center-front detail",
  "matching simple swim bottoms",
  "full body visible from head to feet",
  "relaxed natural standing posture",
  "healthy natural non-sexualized presentation",
  "realistic adult body proportions",
  "natural hands and feet",
  "natural skin texture and pores",
  "soft diffused daylight",
  "clean minimal poolside setting",
  "real professional lifestyle photography",
  "natural perspective",
  "realistic shadows"
].join(", ");

const NEGATIVE_PROMPT = [
  "different face",
  "generic AI face",
  "face beautification",
  "plastic skin",
  "wax skin",
  "over-smoothed skin",
  "wig",
  "helmet hair",
  "artificial hair strands",
  "CGI",
  "anime",
  "glamour pose",
  "sexualized pose",
  "exaggerated chest",
  "exaggerated waist",
  "exaggerated hips",
  "bad anatomy",
  "deformed hands",
  "extra fingers",
  "deformed feet",
  "cropped feet",
  "blurry face"
].join(", ");

const selection = requestFromUser(
  "Hao Local Portrait",
  "Generate",
  function () {
    return [
      this.section(
        "Reference photo",
        "Select one adult reference photo. The photo stays in the local Draw Things workflow.",
        [
          this.imageField("Select reference photo", true)
        ]
      ),
      this.section(
        "Identity strength",
        "Higher values preserve facial identity more strongly. Lower values allow more composition freedom.",
        [
          this.slider(0.74, this.slider.percent, 0.55, 0.90, "Face reference")
        ]
      )
    ];
  }
);

const references = selection[0][0];
if (!references || references.length === 0) {
  console.error("No reference photo selected.");
  return;
}

const faceWeight = selection[1][0];

console.log("Preparing local portrait models...");
pipeline.downloadBuiltins([MODEL, FACE_CONTROL_FILE]);

const configuration = pipeline.configuration;
configuration.model = MODEL;
configuration.width = 512;
configuration.height = 768;
configuration.steps = 28;
configuration.guidanceScale = 6.0;
configuration.seed = -1;
configuration.batchCount = 1;
configuration.batchSize = 1;
configuration.hiresFix = false;
configuration.loras = [];
configuration.controls = [];
configuration.sampler = SamplerType.DPMPP_2M_KARRAS;
configuration.clipSkip = 1;

canvas.clear();
canvas.updateCanvasSize(configuration);
canvas.clearMoodboard();
canvas.loadMoodboardFromSrc(references[0]);
canvas.setMoodboardImageWeight(faceWeight, 0);

const faceControl = pipeline.findControlByName(FACE_CONTROL);
if (!faceControl) {
  console.error("Required face control is unavailable.");
  return;
}

faceControl.weight = faceWeight;
faceControl.guidanceStart = 0.0;
faceControl.guidanceEnd = 0.78;
configuration.controls = [faceControl];

console.log("Generating one local full-body portrait...");
pipeline.run({
  configuration: configuration,
  prompt: PROMPT,
  negativePrompt: NEGATIVE_PROMPT
});

const outputPath = `${filesystem.pictures.path}/hao-local-portrait-${Date.now()}.png`;
canvas.saveImage(outputPath, true);
console.log(`Saved: ${outputPath}`);
