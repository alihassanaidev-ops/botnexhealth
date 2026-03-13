type TimeZoneOption = {
  value: string
  label: string
}

const COMMON_TIMEZONES = [
  "UTC",
  "America/Vancouver",
  "America/Edmonton",
  "America/Winnipeg",
  "America/Regina",
  "America/Toronto",
  "America/Halifax",
  "America/St_Johns",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Phoenix",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "America/Puerto_Rico",
  "Pacific/Guam",
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
const commonTimezones = COMMON_TIMEZONES.filter((tz) => ianaSet.has(tz))
const commonSet = new Set<string>(commonTimezones)
const remainingTimezones = ianaTimezones.filter((tz) => !commonSet.has(tz))

const orderedTimezones = [...commonTimezones, ...remainingTimezones]

export const SUPPORTED_TIMEZONES: TimeZoneOption[] = orderedTimezones.map((tz) => ({
  value: tz,
  label: formatTimeZoneLabel(tz),
}))
