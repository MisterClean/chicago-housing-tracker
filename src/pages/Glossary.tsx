import { Link } from "react-router-dom";

/**
 * Reference page for the terms the data views depend on. Moved off the landing
 * page but kept on the site, since the affordability map is hard to read without
 * knowing what "60% AMI" actually means.
 */
export function Glossary() {
  return (
    <div className="page-narrow">
      <p className="eyebrow">Reference</p>
      <h1 className="page-title">How to read these numbers</h1>
      <p className="page-lede">
        Housing policy runs on jargon. A few terms do most of the work across this site, and getting
        them straight changes what the numbers mean.
      </p>

      <dl className="glossary">
        <div>
          <dt>AMI — Area Median Income</dt>
          <dd>
            The midpoint income for the Chicago region, set annually by the federal government. A
            household at <strong>100% AMI</strong> earns exactly that midpoint; one at{" "}
            <strong>60% AMI</strong> earns 60% of it. Affordability is conventionally measured
            against the 30%-of-income rule: housing is "affordable" to a household if it costs no
            more than 30% of what they earn. So "affordable at 60% AMI" means a household earning
            60% of the regional median could rent it without being cost-burdened.
          </dd>
        </div>
        <div>
          <dt>ARO — Affordable Requirements Ordinance</dt>
          <dd>
            Chicago's rule requiring residential developments of ten or more units that need a
            zoning change, City land or City money to set aside a share of their homes, usually
            20%, at restricted rents, or to pay a fee instead. The units are built by private
            developers inside market-rate buildings, so they land where the market builds. The City
            also publishes maximum rents by unit size, which is why our 60% AMI figures use a{" "}
            <strong>different cap for a studio than for a four-bedroom</strong> — a more accurate
            approach than one flat citywide number, since larger households need larger homes.
          </dd>
        </div>
        <div>
          <dt>CHA — Chicago Housing Authority</dt>
          <dd>
            The City&rsquo;s public housing authority. Since the Plan for Transformation most new
            CHA homes are built inside mixed-income, tax-credit developments on former public
            housing sites, alongside affordable and market-rate units. The Affordable Units page
            counts those units as &ldquo;CHA owned&rdquo; and can show them on their own; CHA housing
            built or bought without tax credits is not covered. Buildings where CHA pays part of
            the rent through project-based vouchers carry a separate &ldquo;CHA voucher&rdquo; label.
          </dd>
        </div>
        <div>
          <dt>Cost-burdened</dt>
          <dd>
            A household spending more than <strong>30% of its income</strong> on housing. Above 50%
            is "severely cost-burdened." It's the standard threshold behind most affordability
            measures, including the ones on this site.
          </dd>
        </div>
        <div>
          <dt>Income-restricted</dt>
          <dd>
            A home whose rent is capped and whose tenants must earn below a set share of AMI,
            usually 80% or less, under a legal agreement that runs for years or decades. This is
            what the Affordable Units page counts. It is different from housing that simply
            happens to be cheap: a low market rent can rise; a restricted one cannot, until the
            agreement ends.
          </dd>
        </div>
        <div>
          <dt>LIHTC — Low-Income Housing Tax Credit</dt>
          <dd>
            The federal program behind most subsidized affordable housing built in the US since
            1986. Investors buy tax credits from a development in exchange for equity, and in return
            the units must stay rent- and income-restricted, generally at or below 60% AMI, for at
            least 30 years. In Chicago the credits are awarded by the City and by the Illinois
            Housing Development Authority (IHDA). <strong>9% credits</strong> are competitive and
            cover more of the cost; <strong>4% credits</strong> come with tax-exempt bonds and are
            often used to rehab existing buildings.
          </dd>
        </div>
        <div>
          <dt>Listings vs. housing stock</dt>
          <dd>
            The affordability map counts <strong>listings</strong> — homes actively on the market
            right now — not all housing in a ward. A ward with cheap listings today can reprice as
            the market moves, and a ward with few listings tells you little about what people
            already living there pay.
          </dd>
        </div>
        <div>
          <dt>New vs. preserved units</dt>
          <dd>
            A <strong>new</strong> affordable unit is one that did not exist as housing before:
            new construction, or a building converted from another use or brought back from long
            vacancy. A <strong>preserved</strong> unit was already housing; public money paid to
            rehab it and to set or renew its rent limits. Preservation keeps affordable homes
            affordable, but it adds no homes, so the Affordable Units page shows new units by
            default and lets you add preserved ones.
          </dd>
        </div>
        <div>
          <dt>Other City/State affordable housing</dt>
          <dd>
            Developments paid for directly by the City (loans, tax-increment financing, land or
            federal Neighborhood Stabilization Program money it administered) or by the Illinois
            Housing Development Authority (HOME funds, trust funds, donation credits), without
            federal tax credits. Many are small supportive-housing or senior
            buildings. The Affordable Units page counts their income-restricted units as a program
            alongside tax credits, CHA and ARO; projects where public money only refinanced
            debt or paid for repairs are left out, since nothing was built, rehabbed or newly
            restricted.
          </dd>
        </div>
        <div>
          <dt>Project-based voucher</dt>
          <dd>
            A rent subsidy tied to a unit rather than to a tenant. CHA contracts with a
            building&rsquo;s owner for a set number of units; the household living in one pays about
            30% of its income and CHA pays the rest, and when that household moves out the subsidy
            stays with the unit for the next one. Tenant-based (Housing Choice, or
            &ldquo;Section 8&rdquo;) vouchers instead move with the household, so they cannot be tied
            to a building and the Affordable Units page leaves them out.
          </dd>
        </div>
        <div>
          <dt>TIF — Tax-increment financing</dt>
          <dd>
            City money drawn from the growth in property taxes inside a designated district and
            spent on projects there, including affordable housing. TIF is usually one source among
            several, so on the Affordable Units page it is a label: the TIF button shows every
            development that received it, whether it is also a tax-credit, Other City/State or ARO
            development.
          </dd>
        </div>
      </dl>

      <p className="stub-back">
        <Link to="/affordability">See the affordability data →</Link>{" "}
        <Link to="/affordable">See affordable units by ward →</Link>
      </p>
    </div>
  );
}
