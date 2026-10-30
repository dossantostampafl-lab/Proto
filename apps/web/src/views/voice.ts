// Browser speech for the ChatGPT explanation panel: no service, no key. Speech recognition is
// Chrome's (Android and desktop); Samsung Internet and Firefox do not have it. Speech synthesis is
// the device's own voice.

type Alternative = { transcript: string };
type RecognitionEvent = { results: ArrayLike<ArrayLike<Alternative>> };
export type Recognition = {
  lang: string; interimResults: boolean; continuous: boolean; maxAlternatives: number;
  onresult: ((e: RecognitionEvent) => void) | null; onend: (() => void) | null;
  onerror: ((e: { error?: string }) => void) | null; start: () => void; abort: () => void;
};

export function recognitionAvailable(): boolean {
  const w = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
  return Boolean(w.SpeechRecognition ?? w.webkitSpeechRecognition);
}

export function newRecognition(): Recognition | null {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.lang = "pt-BR"; rec.interimResults = false; rec.continuous = false; rec.maxAlternatives = 1;
  return rec;
}

export const MIC_ERRORS: Record<string, string> = {
  "not-allowed": "O navegador bloqueou o microfone. Toque no cadeado ao lado do endereço → Permissões → Microfone → Permitir, e recarregue a página.",
  "service-not-allowed": "O navegador bloqueou o reconhecimento de voz. Use o Google Chrome e permita o microfone para este site.",
  "no-speech": "Não ouvi nada. Toque em Falar e fale logo em seguida.",
  "audio-capture": "Nenhum microfone encontrado neste aparelho.",
  network: "O reconhecimento de voz do Chrome precisa de internet. Confira a conexão e tente de novo.",
  aborted: "Escuta interrompida. Toque em Falar de novo.",
  "language-not-supported": "O reconhecimento de voz deste aparelho não aceita português. Atualize o Google/Chrome.",
};

const synth = (): SpeechSynthesis | null => ("speechSynthesis" in window ? window.speechSynthesis : null);

function portugueseVoice(): SpeechSynthesisVoice | null {
  const voices = synth()?.getVoices() ?? [];
  return voices.find(v => v.lang === "pt-BR") ?? voices.find(v => v.lang.toLowerCase().startsWith("pt")) ?? null;
}

/** Chrome on Android speaks only after a user gesture. Called from the tap itself (the answer
 *  arrives seconds later, when the gesture has expired): a silent utterance unlocks speech. */
export function unlockSpeech(): void {
  const s = synth();
  if (!s) return;
  const u = new SpeechSynthesisUtterance(" ");
  u.volume = 0;
  s.speak(u);
}

export function stopSpeaking(): void {
  const s = synth();
  if (s && (s.speaking || s.pending)) s.cancel();
}

/** Short sentences queued one after another: Chrome on Android stops an utterance after about
 *  15 seconds, so a long answer read as one utterance is cut off. */
export function chunks(text: string, size = 180): string[] {
  const sentences = text.replace(/\s+/g, " ").trim().match(/[^.!?;:]+[.!?;:]*\s*/g) ?? [];
  const out: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    if ((current + sentence).length > size && current) { out.push(current.trim()); current = ""; }
    if (sentence.length > size) {
      for (let i = 0; i < sentence.length; i += size) out.push(sentence.slice(i, i + size).trim());
    } else current += sentence;
  }
  if (current.trim()) out.push(current.trim());
  return out.filter(Boolean);
}

export function speak(text: string, onDone?: () => void): boolean {
  const s = synth();
  if (!s) return false;
  stopSpeaking();
  const voice = portugueseVoice();
  const parts = chunks(text.slice(0, 2400));
  parts.forEach((part, i) => {
    const u = new SpeechSynthesisUtterance(part);
    u.lang = "pt-BR";
    if (voice) u.voice = voice;
    if (i === parts.length - 1 && onDone) u.onend = () => onDone();
    s.speak(u);
  });
  // A synthesis queue paused by the system (Android after a call or a long idle) stays silent.
  s.resume();
  return parts.length > 0;
}
