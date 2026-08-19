"""
Shared Pydantic types and helpers reused across multiple entity schema files.

Anything defined here is needed by more than one entity, so it lives in a
neutral module rather than inside any single entity's schema file. This is
what keeps no entity's schema depending on another entity's schema for
something unrelated to that entity's own domain — Tenant needs phone
validation, not anything about Owner, so it imports from here rather than
from app/schemas/owner.py.

The rule for this file: a type belongs here once a SECOND entity needs it.
Types used by exactly one entity stay in that entity's schema module. Keep
this file free of entity-specific schemas (no OwnerBase, no TenantCreate) —
those belong in app/schemas/<entity>.py.

Nothing here touches the database or the service layer; these are pure
validation types at the HTTP boundary.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from pydantic_extra_types.phone_numbers import PhoneNumber


# =============================================================================
# SECTION 2 — DamalPhoneNumber type
# =============================================================================
# Intended for reuse by ANY model that needs phone validation (Owner, Tenant,
# and any future entity with a phone field) — hence the general,
# non-Kenya-exclusive name even though Kenya is merely the default region.
class DamalPhoneNumber(PhoneNumber):
    """Application-wide phone number type.

    Defaults to Kenya (KE) when a client sends a number with no
    explicit country code, but accepts and correctly validates
    international numbers with any country code just as well.
    Always normalizes to E164 format (e.g. "+254707234780") — the
    most compact standard representation, chosen specifically to
    fit within our String(20) database columns (RFC3966, the
    library's other common format, includes a "tel:" prefix and
    hyphens that can exceed 20 characters for some valid numbers).
    """

    default_region_code = "KE"
    phone_format = "E164"
