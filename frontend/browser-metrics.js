// Browser counterpart to backend/app/metrics_analyzer.py. All metrics use RGB;
// alpha is neither compared nor part of the steganographic channel.

export const MAX_ANALYSIS_PIXELS = 2_000_000;

const CHANNEL_NAMES = ["red", "green", "blue"];
const PEAK_VALUE = 255;
const SSIM_C1 = (0.01 * PEAK_VALUE) ** 2;
const SSIM_C2 = (0.03 * PEAK_VALUE) ** 2;
const CHI_SQUARE_INTERPRETATION =
  "Exploratory paired-value histogram heuristic; a p-value is not the " +
  "probability of hidden data and does not establish steganographic or " +
  "cryptographic security. Full-image histograms can dilute localized " +
  "embedding, and natural images can produce similar distributions. " +
  "Sparse pair counts weaken the asymptotic chi-square approximation.";

function comparisonError(code, message) {
  return Object.assign(new Error(message), {code});
}

function validateImage(image) {
  if (!image || !Number.isSafeInteger(image.width) || !Number.isSafeInteger(image.height) ||
      image.width < 1 || image.height < 1) {
    throw comparisonError("INVALID_COMPARISON", "Dimensiunile imaginilor sunt invalide.");
  }
  const pixels = image.width * image.height;
  if (pixels > MAX_ANALYSIS_PIXELS) {
    throw comparisonError("ANALYSIS_TOO_LARGE", "Analiza SSIM acceptă cel mult 2 megapixeli per imagine.");
  }
  if (!(image.data instanceof Uint8Array || image.data instanceof Uint8ClampedArray) ||
      image.data.length !== pixels * 4) {
    throw comparisonError("INVALID_COMPARISON", "Datele RGBA ale imaginii sunt invalide.");
  }
}

// Each chi-square degrees-of-freedom value is an integer in [1, 128].
// Precompute log Γ(df/2) by recurrence from Γ(1/2) = √π and Γ(1) = 1.
const LOG_GAMMA_HALF = new Float64Array(129);
LOG_GAMMA_HALF[1] = Math.log(Math.PI) / 2;
for (let df = 3; df < LOG_GAMMA_HALF.length; df += 1) {
  LOG_GAMMA_HALF[df] = LOG_GAMMA_HALF[df - 2] + Math.log(df / 2 - 1);
}

function chiSquareSurvival(statistic, degrees) {
  if (degrees === 0) return null;
  if (statistic === 0) return 1;
  // Chi-square survival is Q(df/2, statistic/2). For the two expansions, see
  // https://dlmf.nist.gov/8.7.E3 and https://dlmf.nist.gov/8.9.E2.
  const a = degrees / 2;
  const x = statistic / 2;
  const scale = (a * Math.log(x)) - x - LOG_GAMMA_HALF[degrees];
  const epsilon = 2e-15;
  let result;
  if (x < a + 1) {
    // Series for P(a, x), when Q = 1 − P is large enough to avoid cancellation.
    let term = 1 / a;
    let sum = term;
    for (let k = 1; k <= 256; k += 1) {
      term *= x / (a + k);
      sum += term;
      if (term <= sum * epsilon) break;
    }
    result = 1 - Math.exp(scale + Math.log(sum));
  } else {
    // Modified Lentz continued fraction computes Q(a, x) directly in the tail.
    const floor = 1e-300;
    let b = x + 1 - a;
    let c = 1 / floor;
    let d = 1 / b;
    let h = d;
    for (let k = 1; k <= 256; k += 1) {
      const numerator = k * (a - k);
      b += 2;
      d = b + numerator * d;
      c = b + numerator / c;
      if (Math.abs(d) < floor) d = d < 0 ? -floor : floor;
      if (Math.abs(c) < floor) c = c < 0 ? -floor : floor;
      d = 1 / d;
      const delta = c * d;
      h *= delta;
      if (Math.abs(delta - 1) <= epsilon) break;
    }
    result = Math.exp(scale + Math.log(h));
  }
  return Math.max(0, Math.min(1, result));
}

function summarizeHistogram(histogram) {
  const channels = {};
  for (let channel = 0; channel < CHANNEL_NAMES.length; channel += 1) {
    let degrees = 0;
    let sparsePairs = 0;
    let statistic = 0;
    let evenCount = 0;
    let oddCount = 0;
    const start = channel * 256;
    for (let bin = start; bin < start + 256; bin += 2) {
      const even = histogram[bin];
      const odd = histogram[bin + 1];
      const count = even + odd;
      if (count === 0) continue;
      degrees += 1;
      if (count < 10) sparsePairs += 1;
      statistic += (even - odd) ** 2 / count;
      evenCount += even;
      oddCount += odd;
    }
    channels[CHANNEL_NAMES[channel]] = {
      statistic,
      degrees_of_freedom: degrees,
      p_value: chiSquareSurvival(statistic, degrees),
      sample_count: evenCount + oddCount,
      nonempty_pairs: degrees,
      pairs_with_expected_count_below_5: sparsePairs,
      even_count: evenCount,
      odd_count: oddCount,
    };
  }
  return channels;
}

function structuralSimilarity(original, stego, width, height, windowSize) {
  // Slide across the shorter side to bound scratch space by O(min(W, H)).
  // Swapping axes changes traversal order only; every valid square window is
  // still evaluated. The reflected edge values used by SciPy are cropped out.
  const transposed = width > height;
  const columns = Math.min(width, height);
  const rows = Math.max(width, height);
  const columnStride = transposed ? width * 4 : 4;
  const rowStride = transposed ? 4 : width * 4;
  const columnSums = new Uint32Array(columns * 5);
  const samples = windowSize * windowSize;
  const covarianceBias = samples / (samples - 1);
  const windows = (columns - windowSize + 1) * (rows - windowSize + 1);
  let average = 0;

  for (let channel = 0; channel < CHANNEL_NAMES.length; channel += 1) {
    columnSums.fill(0);
    for (let row = 0; row < windowSize; row += 1) {
      let offset = row * rowStride + channel;
      for (let column = 0; column < columnSums.length; column += 5, offset += columnStride) {
        const x = original[offset];
        const y = stego[offset];
        columnSums[column] += x;
        columnSums[column + 1] += y;
        columnSums[column + 2] += x * x;
        columnSums[column + 3] += y * y;
        columnSums[column + 4] += x * y;
      }
    }

    let sum = 0;
    let compensation = 0;
    for (let top = 0; top <= rows - windowSize; top += 1) {
      let xSum = 0;
      let ySum = 0;
      let xxSum = 0;
      let yySum = 0;
      let xySum = 0;
      for (let column = 0; column < windowSize * 5; column += 5) {
        xSum += columnSums[column];
        ySum += columnSums[column + 1];
        xxSum += columnSums[column + 2];
        yySum += columnSums[column + 3];
        xySum += columnSums[column + 4];
      }
      for (let left = 0; ; left += 5) {
        const ux = xSum / samples;
        const uy = ySum / samples;
        const vx = covarianceBias * (xxSum / samples - ux * ux);
        const vy = covarianceBias * (yySum / samples - uy * uy);
        const vxy = covarianceBias * (xySum / samples - ux * uy);
        const score = ((2 * ux * uy + SSIM_C1) * (2 * vxy + SSIM_C2)) /
          ((ux * ux + uy * uy + SSIM_C1) * (vx + vy + SSIM_C2));
        // Compensated accumulation keeps precision across millions of windows.
        const corrected = score - compensation;
        const next = sum + corrected;
        compensation = (next - sum) - corrected;
        sum = next;

        const enter = left + windowSize * 5;
        if (enter === columnSums.length) break;
        xSum += columnSums[enter] - columnSums[left];
        ySum += columnSums[enter + 1] - columnSums[left + 1];
        xxSum += columnSums[enter + 2] - columnSums[left + 2];
        yySum += columnSums[enter + 3] - columnSums[left + 3];
        xySum += columnSums[enter + 4] - columnSums[left + 4];
      }

      if (top === rows - windowSize) break;
      // The next row of windows drops the current top and adds one bottom row.
      let leaving = top * rowStride + channel;
      let entering = leaving + windowSize * rowStride;
      for (let column = 0; column < columnSums.length; column += 5) {
        const oldX = original[leaving];
        const oldY = stego[leaving];
        const newX = original[entering];
        const newY = stego[entering];
        columnSums[column] += newX - oldX;
        columnSums[column + 1] += newY - oldY;
        columnSums[column + 2] += newX * newX - oldX * oldX;
        columnSums[column + 3] += newY * newY - oldY * oldY;
        columnSums[column + 4] += newX * newY - oldX * oldY;
        leaving += columnStride;
        entering += columnStride;
      }
    }
    average += sum / windows;
  }
  return average / CHANNEL_NAMES.length;
}

/** Compare two decoded RGBA byte buffers using the REST API's result format. */
export function analyze(original, stego) {
  validateImage(original);
  validateImage(stego);
  const {width, height, data} = original;
  if (width !== stego.width || height !== stego.height) {
    throw comparisonError("INVALID_COMPARISON", "Imaginile trebuie să aibă aceleași dimensiuni pentru analiză.");
  }
  const shortestSide = Math.min(width, height);
  if (shortestSide < 3) {
    throw comparisonError("INVALID_COMPARISON", "Analiza SSIM necesită imagini de cel puțin 3 × 3 pixeli.");
  }
  const windowSize = Math.min(7, shortestSide % 2 === 0 ? shortestSide - 1 : shortestSide);
  const output = stego.data;
  const originalHistogram = new Uint32Array(256 * 3);
  const stegoHistogram = new Uint32Array(256 * 3);
  let squaredError = 0;
  let changed = 0;
  for (let offset = 0; offset < data.length; offset += 4) {
    for (let channel = 0; channel < CHANNEL_NAMES.length; channel += 1) {
      const from = data[offset + channel];
      const to = output[offset + channel];
      originalHistogram[channel * 256 + from] += 1;
      stegoHistogram[channel * 256 + to] += 1;
      const difference = from - to;
      squaredError += difference * difference;
      if (difference !== 0) changed += 1;
    }
  }
  const channelValues = CHANNEL_NAMES.length * width * height;
  const identical = changed === 0;
  const mse = squaredError / channelValues;
  return {
    psnr_db: identical ? null : 10 * Math.log10(PEAK_VALUE ** 2 / mse),
    psnr_infinite: identical,
    ssim: identical ? 1 : structuralSimilarity(data, output, width, height, windowSize),
    mse,
    width,
    height,
    channels: CHANNEL_NAMES.length,
    changed_channels: changed,
    changed_channel_ratio: changed / channelValues,
    ssim_window_size: windowSize,
    chi_square: {
      method: "paired-value-pearson-full-image-rgb",
      formula: "sum((count_even - count_odd)^2 / (count_even + count_odd))",
      interpretation: CHI_SQUARE_INTERPRETATION,
      original: summarizeHistogram(originalHistogram),
      stego: summarizeHistogram(stegoHistogram),
    },
  };
}
