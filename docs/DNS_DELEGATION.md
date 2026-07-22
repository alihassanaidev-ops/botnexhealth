# DNS delegation request — production subdomains

**To:** scalenexus.ai domain manager
**Requested by:** ScaleNexus engineering
**What we need:** two subdomain delegations added to the `scalenexus.ai` DNS zone

We host the production app's DNS in AWS Route 53. To bring up the production
frontend and API, please delegate two subdomains to our nameservers by adding
the **NS records** below. This is the same kind of delegation already in place
for `staging.scalenexus.ai`, so nothing about the apex domain, the website, or
email changes — only these two subdomains are delegated.

---

## Records to add

### 1. `app.scalenexus.ai`  (record type: **NS**)

```
ns-296.awsdns-37.com
ns-685.awsdns-21.net
ns-1380.awsdns-44.org
ns-1808.awsdns-34.co.uk
```

### 2. `api.scalenexus.ai`  (record type: **NS**)

```
ns-373.awsdns-46.com
ns-683.awsdns-21.net
ns-1467.awsdns-55.org
ns-1970.awsdns-54.co.uk
```

> In most DNS panels: add a new record, **Type = NS**, **Name/Host =**
> `app` (then a second one for `api`), and paste the four nameserver values.
> If the panel asks for one value per line or per record, add all four.

That's the only change required. Once saved, no further action is needed from
your side for these subdomains — certificates and routing are managed
automatically on our end.

---

## How to verify (either side)

After the records propagate (usually minutes, up to a few hours), this returns
the four `awsdns` nameservers for each subdomain:

```bash
dig NS app.scalenexus.ai +short
dig NS api.scalenexus.ai +short
```

---

## Internal reference (ScaleNexus engineering — not for the domain manager)

These subdomains map to the production environment, mirroring the existing
staging layout:

| Subdomain | Serves | Backed by | Route 53 Zone ID |
|---|---|---|---|
| `app.scalenexus.ai` | Production frontend (also proxies `/api/*`) | CloudFront | `Z059877223PXJ71V6XGVO` |
| `api.scalenexus.ai` | Production API origin | ALB | `Z06055691JWDIUN3E32ZC` |

Staging equivalents (already delegated): `staging.scalenexus.ai` (frontend) and
`api.staging.scalenexus.ai` (API).

- **AWS account:** `287553036543` (where staging scalenexus also runs).
- Both hosted zones are public and currently empty except for their SOA/NS
  records. When the production stack is deployed, CDK creates the ACM
  certificates (DNS-validated inside these zones) and the alias records to
  CloudFront / the ALB — no manual record management and no further
  domain-manager requests.
- **Caveat:** if production is ever moved to a *separate* AWS account, these
  zones (and therefore the NS values above) must be recreated there and the
  delegation re-sent. As long as prod stays in `287553036543`, this is a
  one-time request.
