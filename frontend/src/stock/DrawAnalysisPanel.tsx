// 自動畫線判讀卡（T-323）：K 線卡算好的判讀摘要與清單，獨立成一張可拖拉的卡。
import { DrawReadout, SummaryCard, type DrawAnalysis } from '../charts'
import { TPanel } from './ui'

export function DrawAnalysisPanel({ no, analysis }: { no: string; analysis: DrawAnalysis | null }) {
  return (
    <TPanel no={no} title="自動畫線判讀" sub="跟著 K 線卡的視窗（全部／120 日／60 日）與畫線開關 · 只描述圖形，不是買賣訊號">
      {analysis === null ? (
        <div className="dash-state">K 線載入後才有判讀</div>
      ) : (
        <>
          {analysis.summary ? <SummaryCard s={analysis.summary} bare /> : <div className="dash-state">K 棒太少，無法整理摘要</div>}
          {analysis.notes.length > 0 ? (
            <DrawReadout notes={analysis.notes} close={analysis.close} />
          ) : (
            <div className="dash-note">K 線卡的「自動畫線」開關全關，沒有判讀清單。</div>
          )}
        </>
      )}
    </TPanel>
  )
}
