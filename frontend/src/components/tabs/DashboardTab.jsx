import KpiCards from '../KpiCards'
import DataSourceIndicator from '../DataSourceIndicator'
import InteractiveMap from '../InteractiveMap'

export default function DashboardTab({ investigation, selectedMmsi, onSelectVessel }) {
  return (
    <div>
      <div className="section-head">
        <div>
          <span className="eyebrow">Investigation overview</span>
          <h2>Marine pollution intelligence summary</h2>
        </div>
      </div>

      <DataSourceIndicator
        dataSources={investigation.data_sources}
        geolocationSource={investigation.satellite?.geolocation_source}
        geolocationAssumed={investigation.satellite?.geolocation_is_assumed}
      />
      <KpiCards investigation={investigation} />

      <InteractiveMap
        investigation={investigation}
        height={480}
        selectedMmsi={selectedMmsi}
        onSelectVessel={onSelectVessel}
      />

      <p className="caveat-note" style={{ marginTop: 'var(--sp-4)' }}>
        This is an investigative decision-support system. Attribution scores are a transparent
        spatio-temporal correlation index, not a legal finding of fault. The highest-ranked vessel
        is the highest-priority candidate for further investigation based on available evidence.
      </p>
    </div>
  )
}
