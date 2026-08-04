import './Settings.css'
import { normalizeChartSettings } from '../../utils/chartSettings.jsx'

export function Settings({
    id,
    loadedValue,
    isSyncing,
}) {
    const normalizedLoaded = normalizeChartSettings(loadedValue)

    return (
        <section id={id} className='Settings'>
            <div className='group loadedChart'>
                <div className='title'>Loaded chart</div>

                <div className='loadedChartInfo'>
                    <div>{normalizedLoaded.symbol} ({normalizedLoaded.timeframe})</div>
                    <div>History loads on demand</div>
                    <div className={`loadedChartStatus ${isSyncing ? 'syncing' : 'applied'}`}>
                        {isSyncing ? 'Updating chart...' : 'Changes apply automatically'}
                    </div>
                    <div className='loadedChartHint'>
                        Use the indicator button in the header to add or edit indicators.
                    </div>
                </div>
            </div>
        </section>
    )
}
