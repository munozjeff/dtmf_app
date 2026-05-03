/**
 * AudioWorklet processor — captura PCM Float32 del microfono
 * y lo envia al hilo principal en chunks de tamaño configurable.
 */
class DTMFProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    // Tamaño del chunk en muestras: 2048 ~ 42ms a 48kHz
    this._chunkSize = (options.processorOptions || {}).chunkSize || 2048;
    this._buffer    = new Float32Array(this._chunkSize);
    this._writePos  = 0;
  }

  process(inputs) {
    const input   = inputs[0];
    const channel = input && input[0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      this._buffer[this._writePos++] = channel[i];

      if (this._writePos >= this._chunkSize) {
        // Enviar copia al hilo principal
        this.port.postMessage({ pcm: Array.from(this._buffer) });
        this._writePos = 0;
      }
    }
    return true;   // mantener el nodo vivo
  }
}

registerProcessor("dtmf-processor", DTMFProcessor);
