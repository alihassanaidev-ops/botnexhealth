type TimeZoneOption = {
  value: string
  label: string
}

// Canada-first: the full set of Canadian provincial/territorial zones, pinned
// to the top and ordered west→east. Any entries not present in the runtime's
// IANA set (some are aliases in modern tzdb) are filtered out automatically.
const CANADA_TIMEZONES = [
  // Pacific
  "America/Vancouver",
  // Yukon
  "America/Whitehorse",
  "America/Dawson",
  // Mountain
  "America/Edmonton",
  "America/Cambridge_Bay",
  "America/Inuvik",
  "America/Yellowknife",
  "America/Dawson_Creek",
  "America/Fort_Nelson",
  "America/Creston",
  // Central
  "America/Winnipeg",
  "America/Regina",
  "America/Swift_Current",
  "America/Rankin_Inlet",
  "America/Resolute",
  // Eastern
  "America/Toronto",
  "America/Iqaluit",
  "America/Atikokan",
  // Atlantic
  "America/Halifax",
  "America/Moncton",
  "America/Glace_Bay",
  "America/Goose_Bay",
  "America/Blanc-Sablon",
  // Newfoundland
  "America/St_Johns",
] as const

// Province-aware labels so Canadian zones read clearly (instead of "America/…").
const CANADA_LABELS: Record<string, string> = {
  "America/Vancouver": "Pacific Time — Vancouver (BC)",
  "America/Whitehorse": "Yukon Time — Whitehorse (YT)",
  "America/Dawson": "Yukon Time — Dawson (YT)",
  "America/Edmonton": "Mountain Time — Edmonton (AB)",
  "America/Cambridge_Bay": "Mountain Time — Cambridge Bay (NU)",
  "America/Inuvik": "Mountain Time — Inuvik (NT)",
  "America/Yellowknife": "Mountain Time — Yellowknife (NT)",
  "America/Dawson_Creek": "Mountain Time, no DST — Dawson Creek (BC)",
  "America/Fort_Nelson": "Mountain Time, no DST — Fort Nelson (BC)",
  "America/Creston": "Mountain Time, no DST — Creston (BC)",
  "America/Winnipeg": "Central Time — Winnipeg (MB)",
  "America/Regina": "Central Time, no DST — Regina (SK)",
  "America/Swift_Current": "Central Time, no DST — Swift Current (SK)",
  "America/Rankin_Inlet": "Central Time — Rankin Inlet (NU)",
  "America/Resolute": "Central Time — Resolute (NU)",
  "America/Toronto": "Eastern Time — Toronto (ON, QC)",
  "America/Iqaluit": "Eastern Time — Iqaluit (NU)",
  "America/Atikokan": "Eastern Time, no DST — Atikokan (ON)",
  "America/Halifax": "Atlantic Time — Halifax (NS, PEI, NB)",
  "America/Moncton": "Atlantic Time — Moncton (NB)",
  "America/Glace_Bay": "Atlantic Time — Glace Bay (NS)",
  "America/Goose_Bay": "Atlantic Time — Goose Bay (NL · Labrador)",
  "America/Blanc-Sablon": "Atlantic Time, no DST — Blanc-Sablon (QC)",
  "America/St_Johns": "Newfoundland Time — St. John's (NL)",
}

// A few non-Canadian zones kept near the top for cross-border / platform use.
const OTHER_COMMON_TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Phoenix",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
] as const

const REFERENCE_DATE = new Date()

function getIanaTimezones(): string[] {
  const supportedValuesOf = (
    Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
  ).supportedValuesOf

  if (typeof supportedValuesOf === "function") {
    try {
      const values = supportedValuesOf("timeZone")
      if (Array.isArray(values) && values.length > 0) {
        return values
      }
    } catch {
      // Intentionally return empty on failure.
    }
  }

  return []
}

function normalizeOffsetLabel(value: string | undefined): string | null {
  if (!value) return null
  if (value === "GMT" || value === "UTC") return "UTC+00:00"

  const match = value.match(/([+-])(\d{1,2})(?::?(\d{2}))?/)
  if (!match) return null

  const sign = match[1]
  const hours = match[2].padStart(2, "0")
  const minutes = (match[3] ?? "00").padStart(2, "0")
  return `UTC${sign}${hours}:${minutes}`
}

function getOffsetLabel(timeZone: string): string | null {
  const candidates = ["shortOffset", "longOffset"] as const
  for (const timeZoneName of candidates) {
    try {
      const formatter = new Intl.DateTimeFormat("en-US", {
        timeZone,
        hour: "2-digit",
        minute: "2-digit",
        timeZoneName: timeZoneName as Intl.DateTimeFormatOptions["timeZoneName"],
      })
      const parts = formatter.formatToParts(REFERENCE_DATE)
      const tzName = parts.find((part) => part.type === "timeZoneName")?.value
      const normalized = normalizeOffsetLabel(tzName)
      if (normalized) return normalized
    } catch {
      // Continue to next candidate.
    }
  }
  return null
}

function getLongTimeZoneName(timeZone: string): string | null {
  try {
    const formatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      timeZoneName: "long",
    })
    const parts = formatter.formatToParts(REFERENCE_DATE)
    return parts.find((part) => part.type === "timeZoneName")?.value ?? null
  } catch {
    return null
  }
}

function formatTimeZoneLabel(timeZone: string): string {
  const pretty = timeZone.replace(/_/g, " ")
  const longName = getLongTimeZoneName(timeZone)
  const offset = getOffsetLabel(timeZone)

  const segments = [pretty]
  if (longName && longName !== pretty) {
    segments.push(`— ${longName}`)
  }
  if (offset) {
    segments.push(`(${offset})`)
  }
  return segments.join(" ")
}

const ianaTimezones = getIanaTimezones()
const ianaSet = new Set(ianaTimezones)

// Pin Canadian zones first, then a few other common ones, then everything else.
const canadaTimezones = CANADA_TIMEZONES.filter((tz) => ianaSet.has(tz))
const pinnedSet = new Set<string>(canadaTimezones)
const otherCommon = OTHER_COMMON_TIMEZONES.filter(
  (tz) => ianaSet.has(tz) && !pinnedSet.has(tz),
)
otherCommon.forEach((tz) => pinnedSet.add(tz))
const remainingTimezones = ianaTimezones.filter((tz) => !pinnedSet.has(tz))

const orderedTimezones = [...canadaTimezones, ...otherCommon, ...remainingTimezones]

function labelFor(tz: string): string {
  const friendly = CANADA_LABELS[tz]
  if (friendly) {
    const offset = getOffsetLabel(tz)
    return offset ? `${friendly} (${offset})` : friendly
  }
  return formatTimeZoneLabel(tz)
}

export const SUPPORTED_TIMEZONES: TimeZoneOption[] = orderedTimezones.map((tz) => ({
  value: tz,
  label: labelFor(tz),
}))
